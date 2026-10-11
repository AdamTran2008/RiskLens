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

from collections import defaultdict, deque

from . import fetch, gateway, live, market, report, runner, webapp
from .data import load_csv, load_manifest, sha256_file

MAX_BODY = 64 * 1024
RUN_ID_RE = re.compile(r"[0-9a-f]{24}")
MARKET_SYM_RE = re.compile(r"[A-Z0-9.\-=^]{1,15}")

# Public demo mode (--public): limits so one visitor can't hog a shared server
PUBLIC = {"posts_per_minute": 60, "runs_per_minute": 20, "max_luck_trials": 1000,
          "streams_total": 40, "streams_per_client": 4, "max_stored_results": 2000}


class RateLimiter:
    """At most ``per_minute`` events per key in any rolling 60 seconds."""

    def __init__(self, per_minute, clock=time.monotonic):
        self.per, self.clock = per_minute, clock
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key) -> bool:
        with self.lock:
            now = self.clock()
            q = self.hits[key]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per:
                return False
            q.append(now)
            if len(self.hits) > 10_000:
                for k in [k for k, v in self.hits.items() if not v]:
                    del self.hits[k]
            return True


class StreamSlots:
    """Caps concurrent live streams in total and per client."""

    def __init__(self, total, per_client):
        self.total, self.per_client = total, per_client
        self.by_client = defaultdict(int)
        self.lock = threading.Lock()

    def acquire(self, client) -> bool:
        with self.lock:
            if sum(self.by_client.values()) >= self.total or self.by_client[client] >= self.per_client:
                return False
            self.by_client[client] += 1
            return True

    def release(self, client):
        with self.lock:
            self.by_client[client] -= 1
            if self.by_client[client] <= 0:
                del self.by_client[client]


class App:
    def __init__(self, data_dir: str, results_dir: str, public: bool = False):
        self.public = public
        self.post_limit = RateLimiter(PUBLIC["posts_per_minute"])
        self.run_limit = RateLimiter(PUBLIC["runs_per_minute"])
        self.streams = StreamSlots(PUBLIC["streams_total"], PUBLIC["streams_per_client"])
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
        self.gateway = gateway.Gateway()
        self.fetcher = fetch.yahoo_daily          # replaced in tests
        self._data_lock = threading.Lock()

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
        if self.public:
            self._prune()

    def _prune(self):
        """Public mode: keep the store bounded (results can always be recomputed)."""
        files = [f for f in os.listdir(self.results_dir) if f.endswith(".json")]
        if len(files) <= PUBLIC["max_stored_results"]:
            return
        files.sort(key=lambda f: os.path.getmtime(os.path.join(self.results_dir, f)))
        for f in files[:len(files) - PUBLIC["max_stored_results"] + 200]:
            try:
                os.remove(os.path.join(self.results_dir, f))
            except OSError:
                pass

    # --------------------------------------------------------- overview
    def overview(self):
        """Risk snapshot of every dataset, from a plain buy-and-hold run."""
        cards = []
        for sym in sorted(self.datasets):
            r, _ = self.submit({"symbol": sym, "strategy": {"name": "buy_and_hold"},
                                "luck_test": {"trials": 0}})
            s = r["series"]
            close = s["close"]
            n = len(close)
            y = min(252, n - 1)
            rets = [close[i] / close[i - 1] - 1 for i in range(n - y, n)]
            m = sum(rets) / len(rets)
            vol = (sum((x - m) ** 2 for x in rets) / max(1, len(rets) - 1)) ** 0.5 * 252 ** 0.5
            step = max(1, y // 60)
            cur_dd = s["drawdown"][-1]
            cards.append({
                "symbol": sym, "source": self.datasets[sym].get("source", ""),
                "first": s["dates"][0], "last": s["dates"][-1], "price": close[-1] / 100,
                "ret_1y": close[-1] / close[n - 1 - y] - 1, "vol_1y": vol,
                "drawdown_now": cur_dd, "max_drawdown": r["summary"]["max_drawdown"],
                "var_95": r["summary"]["var_95"], "cagr": r["summary"]["cagr"],
                "status": "stressed" if cur_dd >= 0.15 else "elevated" if cur_dd >= 0.05 else "calm",
                "spark": [close[i] / 100 for i in range(n - 1 - y, n, step)],
            })
        return cards

    def history(self, limit=60):
        files = sorted((f for f in os.listdir(self.results_dir) if f.endswith(".json")),
                       key=lambda f: os.path.getmtime(os.path.join(self.results_dir, f)),
                       reverse=True)[:limit]
        out = []
        for f in files:
            r = self.load(f[:-5])
            if not r:
                continue
            c = r["config"]
            if c["strategy"]["name"] == "buy_and_hold" and not c["luck_test"]["trials"] \
                    and not (c["risk"]["vol_target"] or c["risk"]["max_drawdown"]):
                continue                     # internal overview snapshot, not a user analysis
            out.append({"run_id": r["run_id"], "symbol": c["symbol"], "strategy": c["strategy"],
                        "risk": c["risk"], "start": r["summary"]["start"], "end": r["summary"]["end"],
                        "cagr": r["summary"]["cagr"], "max_drawdown": r["summary"]["max_drawdown"],
                        "sharpe": r["summary"]["sharpe"], "volatility": r["summary"]["volatility"],
                        "saved": time.strftime("%Y-%m-%d %H:%M", time.localtime(
                            os.path.getmtime(os.path.join(self.results_dir, f))))})
        return out

    # ---------------------------------------------------------- datasets
    def add_dataset(self, body):
        """Download a stock's daily prices, freeze and register them, and make
        them available immediately (no restart). Local use only."""
        if self.public:
            raise PermissionError("Adding stocks is only available when you run RiskLens on your "
                                  "own computer (Yahoo data can't be redistributed).")
        if type(body) is not dict or set(body) - {"symbol", "start", "end"}:
            raise fetch.FetchError("send {\"symbol\": ..., \"start\": optional, \"end\": optional}")
        with self._data_lock:                      # one download at a time; manifest stays consistent
            e = fetch.add_dataset(self.data_dir, body.get("symbol"), body.get("start"),
                                  body.get("end"), fetcher=self.fetcher)
            sym = fetch.check_symbol(body.get("symbol"))
            self.datasets[sym] = dict(e, path=os.path.join(self.data_dir, e["file"]))
        return {"symbol": sym, **e}

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
        disable_nagle_algorithm = True     # send each streamed update immediately
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

        def _live(self, q):
            try:
                raw = json.loads(q.get("cfg", ["{}"])[0])
                cfg = runner.normalize(raw)
                ds = app.datasets.get(cfg["symbol"])
                if ds is None:
                    raise runner.ConfigError(f"no dataset for {cfg['symbol']}")
                speed = min(2000.0, max(1.0, float(q.get("speed", ["40"])[0])))
                start = max(0, int(q.get("from", ["0"])[0]))
            except (ValueError, json.JSONDecodeError) as e:
                return self._err(400, str(e))
            slot = self._stream_slot()
            if slot is None:
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def emit(kind, payload):
                    try:
                        self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionError, OSError):
                        raise live.ClientGone()
                try:
                    live.stream(cfg, ds["path"], emit, start=start, speed=speed)
                except live.ClientGone:
                    pass
            finally:
                if app.public:
                    app.streams.release(slot)

        def _market(self, q):
            sym = q.get("symbol", ["BTC-USD"])[0].upper()
            source = q.get("source", ["auto"])[0]
            try:
                if not MARKET_SYM_RE.fullmatch(sym):
                    raise ValueError("symbol must be 1-15 letters, digits or . - = ^")
                vol = float(q.get("vol", ["0.15"])[0])
                dd = float(q.get("dd", ["0.15"])[0])
                if not (0.01 <= vol <= 2 and 0.005 <= dd <= 0.99):
                    raise ValueError("vol must be 0.01-2 and dd 0.005-0.99")
                provider, poll = market.make_provider(source, sym, public=app.public)
            except ValueError as e:
                return self._err(400, str(e))
            slot = self._stream_slot()
            if slot is None:
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def emit(kind, payload):
                    try:
                        self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionError, OSError):
                        raise market.ClientGone()
                try:
                    market.stream(sym, provider, emit, vol_target=vol, dd_limit=dd, poll_seconds=poll,
                                  on_quote=app.gateway.mark)
                except market.ClientGone:
                    pass
            finally:
                if app.public:
                    app.streams.release(slot)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/market":
                return self._market(parse_qs(u.query))
            if u.path in ("/", "/app"):
                return self._send(200, webapp.page(app.version), "text/html")
            if u.path == "/api/overview":
                return self._send(200, app.overview())
            if u.path == "/api/history":
                return self._send(200, app.history())
            if u.path == "/api/live":
                return self._live(parse_qs(u.query))
            if u.path == "/api/gateway":
                try:
                    return self._send(200, app.gateway.state(self._sid()))
                except gateway.GatewayError as e:
                    return self._err(400, str(e))
            if u.path == "/health":
                return self._send(200, {"ok": True, "engine_version": app.version, "public": app.public,
                                        **app.stats})
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
                embed = parse_qs(u.query).get("embed", ["0"])[0] == "1"
                return self._send(200, report.render(runs, embed=embed), "text/html")
            return self._err(404, "not found")

        def _body(self, allow_empty=False):
            """Parsed JSON body, or None after sending an error response."""
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._err(400, "bad Content-Length")
                return None
            if allow_empty and n == 0:
                return {}
            if n <= 0 or n > MAX_BODY:
                self._err(413, f"body must be 1..{MAX_BODY} bytes")
                return None
            try:
                return json.loads(self.rfile.read(n))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._err(400, "body is not valid JSON")
                return None

        def _client(self):
            if app.public:                     # behind the host's proxy
                fwd = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                if fwd:
                    return fwd
            return self.client_address[0]

        def _stream_slot(self):
            """Public mode: claim a live-stream slot, or send 429 and return None."""
            if not app.public:
                return "local"
            c = self._client()
            if not app.streams.acquire(c):
                self._err(429, "too many live streams open; close another tab and retry")
                return None
            return c

        def _sid(self):
            return self.headers.get("X-RiskLens-Session") or "local-default"

        def _gateway_post(self, path):
            body = self._body(allow_empty=True)
            if body is None:
                return
            gw, sid = app.gateway, self._sid()
            try:
                if path == "/api/gateway/order":
                    d = gw.submit(sid, body)
                    return self._send(200, {"decision": d, "state": gw.state(sid)})
                if path == "/api/gateway/kill":
                    return self._send(200, gw.kill(sid, flatten=body.get("flatten", True) is not False))
                if path == "/api/gateway/rearm":
                    return self._send(200, gw.rearm(sid))
                if path == "/api/gateway/reset":
                    return self._send(200, gw.reset(sid))
                if path == "/api/gateway/limits":
                    return self._send(200, gw.set_limits(sid, body))
            except gateway.GatewayError as e:
                return self._err(400, str(e))
            return self._err(404, "not found")

        def do_POST(self):
            path = urlparse(self.path).path
            if app.public and not app.post_limit.allow(self._client()):
                return self._err(429, "too many requests; wait a minute")
            if path.startswith("/api/gateway/"):
                return self._gateway_post(path)
            if path == "/api/datasets":
                body = self._body()
                if body is None:
                    return
                try:
                    return self._send(200, app.add_dataset(body))
                except PermissionError as e:
                    return self._err(403, str(e))
                except (fetch.FetchError, ValueError) as e:
                    return self._err(400, str(e))
            if path != "/runs":
                return self._err(404, "not found")
            cfg = self._body()
            if cfg is None:
                return
            if app.public:
                lt = cfg.get("luck_test") if isinstance(cfg, dict) else None
                trials = lt.get("trials", 1000) if isinstance(lt, dict) else 1000
                if type(trials) is int and trials > PUBLIC["max_luck_trials"]:
                    return self._err(400, f"the public demo runs at most {PUBLIC['max_luck_trials']:,} "
                                          f"luck-test shuffles")
                if not app.run_limit.allow(self._client()):
                    return self._err(429, "too many analyses this minute; wait a moment")
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


class _Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second server bind the same port while
    # the first keeps receiving requests. Refuse instead, so a stale server
    # can never silently answer.
    allow_reuse_address = os.name != "nt"


def serve(host, port, data_dir, results_dir, quiet=True, public=False):
    app = App(data_dir, results_dir, public=public)
    try:
        httpd = _Server((host, port), make_handler(app, quiet))
    except OSError as e:
        raise SystemExit(f"cannot listen on {host}:{port} ({e}). Is another RiskLens "
                         f"server already running? Stop it with Ctrl+C or use --port.")
    httpd.daemon_threads = True
    return app, httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description="RiskLens run server")
    ap.add_argument("--host", default=None, help="default 127.0.0.1, or 0.0.0.0 with --public")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)),
                    help="default $PORT if set (hosting services), else 8765")
    ap.add_argument("--public", action="store_true",
                    help="public demo: listen on all interfaces, rate limits, capped streams and store")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    a.host = a.host or ("0.0.0.0" if a.public else "127.0.0.1")
    app, httpd = serve(a.host, a.port, a.data_dir, a.results_dir, quiet=not a.verbose,
                       public=a.public)
    names = ", ".join(f"{s}({e['rows']})" for s, e in app.datasets.items()) or "none"
    print(f"RiskLens server on http://{a.host}:{a.port}  engine={app.version}  "
          f"datasets={names}", flush=True)
    shown = "127.0.0.1" if a.host == "0.0.0.0" else a.host
    print(f"Open the app in your browser: http://{shown}:{a.port}/"
          + ("  (public demo mode: rate limits on)" if a.public else ""), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
