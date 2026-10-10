"""RiskLens run server: a small HTTP/JSON service that owns the data and
the result store.

    python -m risklens.server --port 8765

Why a separate server: everyone on a team submits runs to one place, which
holds the canonical data snapshots and an immutable, content-addressed
result store. Two people asking for the same run get the identical stored
result (same ID) instead of two slightly different local runs.

Endpoints
  GET  /health
  GET  /datasets                    symbols with row counts, dates, SHA-256
  POST /runs        {config}        validate, run (or return cached), summary
  GET  /runs/<id>                   full stored result
  GET  /report?runs=<id>[,<id>]     HTML risk report comparing runs

Safety: binds 127.0.0.1 by default, 64 KB request limit, strict config
validation (unknown fields rejected), run IDs are hex-only (no path
traversal), results written atomically (temp file + rename), and concurrent
requests for the same run are computed once.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import report, runner
from .data import load_csv, load_manifest, sha256_file

MAX_BODY = 64 * 1024
RUN_ID_RE = re.compile(r"[0-9a-f]{24}")


class App:
    def __init__(self, data_dir: str, results_dir: str):
        self.data_dir = data_dir
        self.results_dir = results_dir
        os.makedirs(results_dir, exist_ok=True)
        self.version = runner.engine_version()
        self.datasets = {}
        manifest = load_manifest(data_dir)
        for sym, e in sorted(manifest.items()):
            path = os.path.join(data_dir, e["file"])
            if not os.path.exists(path):
                print(f"warning: {sym}: {e['file']} listed in manifest but missing; skipped "
                      f"(run scripts/fetch_data.py {sym})", file=sys.stderr)
                continue
            actual = sha256_file(path)
            if actual != e["sha256"]:
                raise SystemExit(f"{sym}: {e['file']} does not match its manifest hash "
                                 f"(expected {e['sha256'][:12]}, got {actual[:12]}). "
                                 f"Refusing to serve changed data.")
            load_csv(path)   # validates the file
            self.datasets[sym] = dict(e, path=path)
        self._lock = threading.Lock()
        self._run_locks: dict[str, threading.Lock] = {}
        self.stats = {"runs_computed": 0, "cache_hits": 0}

    # ------------------------------------------------------------ store
    def _path(self, rid):
        return os.path.join(self.results_dir, f"{rid}.json")

    def load(self, rid):
        if not RUN_ID_RE.fullmatch(rid):
            return None
        try:
            with open(self._path(rid)) as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def _store(self, rid, result):
        tmp = self._path(rid) + f".tmp{threading.get_ident()}"
        with open(tmp, "w") as f:
            json.dump(result, f, separators=(",", ":"))
        os.replace(tmp, self._path(rid))      # atomic: readers never see half a file

    # -------------------------------------------------------------- run
    def submit(self, raw_cfg: dict):
        cfg = runner.normalize(raw_cfg)
        ds = self.datasets.get(cfg["symbol"])
        if ds is None:
            raise runner.ConfigError(f"no dataset for {cfg['symbol']}; "
                                     f"available: {sorted(self.datasets)}")
        rid = runner.run_id(cfg, ds["sha256"], self.version)
        with self._lock:
            lk = self._run_locks.setdefault(rid, threading.Lock())
        with lk:                                 # same run requested twice: compute once
            existing = self.load(rid)
            if existing is not None:
                with self._lock:
                    self.stats["cache_hits"] += 1
                return existing, True
            result = runner.execute(cfg, ds["path"], ds["sha256"], self.version)
            assert result["run_id"] == rid
            self._store(rid, result)
            with self._lock:
                self.stats["runs_computed"] += 1
            return result, False


def make_handler(app: App, quiet: bool = True):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RiskLens/1.0"

        def log_message(self, fmt, *args):
            if not quiet:
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (
                json.dumps(body, separators=(",", ":")).encode() if ctype == "application/json"
                else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _err(self, code, msg):
            self._send(code, {"error": msg})

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/health":
                return self._send(200, {"ok": True, "engine_version": app.version, **app.stats})
            if u.path == "/datasets":
                return self._send(200, {s: {k: v for k, v in e.items() if k != "path"}
                                        for s, e in app.datasets.items()})
            if u.path.startswith("/runs/"):
                r = app.load(u.path[len("/runs/"):])
                return self._send(200, r) if r else self._err(404, "unknown run id")
            if u.path == "/report":
                ids = parse_qs(u.query).get("runs", [""])[0].split(",")
                runs = [app.load(i) for i in ids if i][:4]
                if not runs or any(r is None for r in runs):
                    return self._err(404, "unknown run id")
                return self._send(200, report.render(runs), "text/html")
            return self._err(404, "not found")

        def do_POST(self):
            if urlparse(self.path).path != "/runs":
                return self._err(404, "not found")
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._err(400, "bad Content-Length")
            if n <= 0 or n > MAX_BODY:
                return self._err(413, f"body must be 1..{MAX_BODY} bytes")
            try:
                cfg = json.loads(self.rfile.read(n))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._err(400, "body is not valid JSON")
            try:
                t0 = time.perf_counter()
                result, cached = app.submit(cfg)
            except runner.ConfigError as e:
                return self._err(400, str(e))
            self._send(200, {"run_id": result["run_id"], "cached": cached,
                             "server_ms": round((time.perf_counter() - t0) * 1000, 2),
                             "config": result["config"], "manifest": result["manifest"],
                             "summary": result["summary"], "luck_test": result["luck_test"],
                             "risk_events": len(result["risk_events"])})

    return Handler


def serve(host, port, data_dir, results_dir, quiet=True):
    app = App(data_dir, results_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(app, quiet))
    httpd.daemon_threads = True
    return app, httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description="RiskLens run server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    app, httpd = serve(a.host, a.port, a.data_dir, a.results_dir, quiet=not a.verbose)
    names = ", ".join(f"{s}({e['rows']})" for s, e in app.datasets.items()) or "none"
    print(f"RiskLens server on http://{a.host}:{a.port}  engine={app.version}  "
          f"datasets={names}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
