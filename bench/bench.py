"""RiskLens benchmarks. Every number in PERFORMANCE.md comes from here.

    python bench/bench.py            # all benchmarks -> bench/results/*.json

1. engine   bars/second vs dataset size (10, 100, 1000 years of daily bars),
            with and without per-day invariant checking; peak memory
2. run      where the time goes in one full 10-year run: load / simulate /
            luck test
3. luck     luck-test time vs number of shuffles
4. server   HTTP latency: cold runs (computed) vs cache hits, 1 and 8
            concurrent clients; server pinned to CPU 0, clients to CPU 1
6. gateway  pre-trade risk check latency: in process, and over HTTP
5. live     live market pipeline: time from a trade message arriving on the
            (fake, in-memory) Coinbase socket to the risk update reaching an
            SSE client over loopback; plus risk-snapshot compute time
Synthetic data (seeded) is used so anyone can reproduce the numbers.
"""
from __future__ import annotations

import json
import os
import platform
import random
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from risklens import metrics, runner  # noqa: E402
from risklens.data import register, synthetic, write_csv  # noqa: E402
from risklens.engine import Costs, RiskConfig, run  # noqa: E402
from risklens.strategies import SMACross  # noqa: E402

OUT = os.path.join(ROOT, "bench", "results")


def env():
    cpu = "unknown"
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        cpu = platform.processor()
    try:
        aff = sorted(os.sched_getaffinity(0))
    except AttributeError:
        aff = None
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "affinity": aff,
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version()}


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, round(p / 100 * (len(v) - 1))))]


def lat(vals_ms):
    return {"p50": round(pct(vals_ms, 50), 2), "p90": round(pct(vals_ms, 90), 2),
            "p99": round(pct(vals_ms, 99), 2), "max": round(max(vals_ms), 2), "n": len(vals_ms)}


def save(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2)
    print(f"== {name}\n{json.dumps(obj, indent=2)}\n", flush=True)


# --------------------------------------------------------------- 1. engine
def bench_engine():
    risk = RiskConfig(vol_target=0.15, max_drawdown=0.15)
    rows = []
    for years in (10, 100, 1000):
        bars = synthetic(252 * years, seed=11)
        for check in (True, False):
            best = None
            for _ in range(3):
                t = time.perf_counter()
                r = run(bars, SMACross(50, 200), costs=Costs(), risk=risk, check=check)
                dt = time.perf_counter() - t
                best = dt if best is None else min(best, dt)
            rows.append({"years": years, "bars": len(bars), "invariant_checks": check,
                         "seconds": round(best, 4), "bars_per_s": round(len(bars) / best),
                         "trades": len(r.trades)})
    bars = synthetic(252 * 100, seed=11)
    tracemalloc.start()
    run(bars, SMACross(50, 200), risk=risk)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # determinism under load: same inputs twice -> same fingerprint
    f1 = run(bars, SMACross(50, 200), risk=risk).fingerprint()
    f2 = run(bars, SMACross(50, 200), risk=risk).fingerprint()
    save("engine.json", {"env": env(), "strategy": "sma_cross(50,200) + vol target 15% + breaker 15%",
                         "results": rows,
                         "peak_traced_memory_mb_100y": round(peak / 1e6, 1),
                         "bytes_per_bar_100y": round(peak / len(bars)),
                         "deterministic_fingerprint_match": f1 == f2})


# ---------------------------------------------------------- 2/3. run, luck
def bench_run_and_luck(tmp):
    write_csv(os.path.join(tmp, "SYNTH.csv"), synthetic(2520, seed=7))
    cfg = runner.normalize({"symbol": "SYNTH", "strategy": {"name": "sma_cross",
                            "params": {"fast": 50, "slow": 200}},
                            "risk": {"vol_target": 0.15, "max_drawdown": 0.15}})
    timings = [runner.execute(cfg, os.path.join(tmp, "SYNTH.csv"))["timing_ms"] for _ in range(5)]
    best = min(timings, key=lambda t: t["total"])
    save("run_breakdown.json", {"env": env(), "workload": "10 years (2,520 bars), 1,000 luck shuffles",
                                "best_of_5_ms": best})
    rng = random.Random(1)
    a = [rng.gauss(0, 0.01) for _ in range(2519)]
    e = [rng.choice([0.0, 1.0]) for _ in range(2519)]
    rows = []
    for trials in (100, 1000, 5000):
        t = time.perf_counter()
        metrics.luck_test(a, e, trials=trials, seed=1)
        rows.append({"trials": trials, "seconds": round(time.perf_counter() - t, 3)})
    save("luck_test.json", {"env": env(), "days": 2519, "results": rows})


# -------------------------------------------------------------- 4. server
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post(base, cfg):
    req = urllib.request.Request(base + "/runs", data=json.dumps(cfg).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())
    return (time.perf_counter() - t) * 1000, body


def bench_server(tmp):
    data = os.path.join(tmp, "data")
    os.makedirs(data, exist_ok=True)
    write_csv(os.path.join(data, "SYNTH.csv"), synthetic(2520, seed=7))
    register(data, "SYNTH", "SYNTH.csv", source="synthetic seed=7")
    port = free_port()
    cmd = [sys.executable, "-m", "risklens.server", "--port", str(port), "--data-dir", data,
           "--results-dir", os.path.join(tmp, "results")]
    if shutil.which("taskset"):
        cmd = ["taskset", "-c", "0"] + cmd
    if hasattr(os, "sched_setaffinity") and (os.cpu_count() or 1) > 1:
        os.sched_setaffinity(0, {1})
    srv = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health").read()
                break
            except OSError:
                time.sleep(0.05)
        cold, cold_srv = [], []
        for i in range(20):      # 20 distinct configs -> each one computed
            ms, body = post(base, {"symbol": "SYNTH", "strategy": {"name": "sma_cross",
                                   "params": {"fast": 20 + i, "slow": 200}},
                                   "risk": {"vol_target": 0.15, "max_drawdown": 0.15}})
            assert not body["cached"]
            cold.append(ms)
            cold_srv.append(body["server_ms"])
        hot_cfg = {"symbol": "SYNTH", "strategy": {"name": "sma_cross",
                   "params": {"fast": 20, "slow": 200}},
                   "risk": {"vol_target": 0.15, "max_drawdown": 0.15}}
        hot1 = [post(base, hot_cfg)[0] for _ in range(500)]
        hot8: list[float] = []
        lock = threading.Lock()

        def worker():
            mine = [post(base, hot_cfg)[0] for _ in range(250)]
            with lock:
                hot8.extend(mine)
        t = time.perf_counter()
        ts = [threading.Thread(target=worker) for _ in range(8)]
        [x.start() for x in ts]
        [x.join() for x in ts]
        wall = time.perf_counter() - t
        health = json.loads(urllib.request.urlopen(base + "/health").read())
        rss = None
        try:
            for line in open(f"/proc/{srv.pid}/status"):
                if line.startswith("VmHWM"):
                    rss = round(int(line.split()[1]) / 1024, 1)
        except OSError:
            pass
    finally:
        srv.terminate()
        srv.wait(10)
    save("server.json", {
        "env": env() | {"server_cpu": 0, "client_cpu": 1, "transport": "HTTP/1.0 over loopback"},
        "workload": "10-year dataset; cold = 20 distinct configs each computed (1,000 luck shuffles); "
                    "hot = repeated identical config served from the result store",
        "cold_run_ms": lat(cold), "cold_server_compute_ms": lat(cold_srv),
        "cache_hit_1_client_ms": lat(hot1),
        "cache_hit_8_clients_ms": lat(hot8),
        "cache_hit_8_clients_req_per_s": round(len(hot8) / wall),
        "server_stats": health, "server_peak_rss_mb": rss})


# ------------------------------------------------------------- 5. live feed
def bench_live(tmp, n=200):
    """Server-side latency of the live market pipeline, excluding the internet:
    trade message handed to the socket reader -> SSE "tick" line read by the client."""
    import http.client
    import queue
    from risklens import market, server as srv

    class Sock:                                  # stands in for Coinbase's WebSocket
        def __init__(self):
            self.q, self.closed = queue.Queue(), False

        def send(self, s):
            pass

        def recv(self):
            while not self.closed:
                try:
                    return self.q.get(timeout=0.05)
                except queue.Empty:
                    pass
            raise ConnectionError("closed")

        def close(self):
            self.closed = True

    sock = Sock()

    def candles(product):
        rng, px, t0, rows = random.Random(5), 60000.0, int(time.time()) // 60 * 60 - 300 * 60, []
        for i in range(300):
            o = px
            px *= 1 + rng.gauss(0, 0.0008)
            rows.append([t0 + i * 60, min(o, px), max(o, px), o, px, 1.0])
        return rows[::-1]

    snap = {}
    bars = [(f"{i:06d}", 100.0, 101.0, 99.0, 100 + (i % 7) * 0.1, 1.0) for i in range(1440)]
    for k in (300, 1440):
        ts = []
        for _ in range(2000):
            t0 = time.perf_counter()
            market.snapshot("BTC-USD", bars[:k])
            ts.append((time.perf_counter() - t0) * 1000)
        snap[f"{k}_bars"] = {key: round(v, 3) for key, v in lat(ts).items() if key != "n"}

    orig = market.make_provider
    market.make_provider = lambda source, sym, public=False: (
        market.CoinbaseProvider(connect=lambda url: sock, fetch_candles=candles), 15.0)
    d = os.path.join(tmp, "live-data")
    os.makedirs(d)
    app, httpd = srv.serve("127.0.0.1", 0, d, os.path.join(tmp, "live-res"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", "/api/market?symbol=BTC-USD&source=coinbase")
        resp = conn.getresponse()
        fp = resp.fp

        def next_event():
            kind = None
            while True:
                line = fp.readline()
                if line.startswith(b"event: "):
                    kind = line[7:].strip().decode()
                elif line.startswith(b"data: "):
                    return kind, line[6:]
        assert next_event()[0] == "snap"
        e2e, compute, px = [], [], 60000.0
        for i in range(n):
            px *= 1.0002 if i % 2 else 0.9998
            t = time.time()
            msg = json.dumps({"type": "ticker", "product_id": "BTC-USD", "price": f"{px:.2f}",
                              "last_size": "0.01", "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))
                              + f".{int(t % 1 * 1e6):06d}Z"})
            t0 = time.perf_counter()
            sock.q.put(msg)
            while True:
                kind, data = next_event()
                if kind == "tick":
                    break
            e2e.append((time.perf_counter() - t0) * 1000)
            compute.append(json.loads(data)["lat"]["compute_ms"])
            time.sleep(0.12)                     # > the 100 ms batching window: one tick per trade
        conn.close()
    finally:
        market.make_provider = orig
        httpd.shutdown()
        httpd.server_close()
    save("live.json", {
        "env": env(),
        "workload": f"{n} trades, one every 120 ms, BTC-USD session of 300 backfilled 1-minute bars; "
                    "fake in-memory Coinbase socket; SSE client in the same process over loopback",
        "trade_to_browser_socket_ms": lat(e2e),
        "risk_snapshot_compute_ms_in_pipeline": lat(compute),
        "risk_snapshot_compute_ms_microbench": snap,
        "not_included": "internet time from Coinbase to your machine (shown live in the app as "
                        "'Exchange -> server') and browser rendering"})


# ---------------------------------------------------------- 6. gateway
def bench_gateway(tmp, n=10_000, n_http=1000):
    """Pre-trade check latency: all eight checks plus the simulated fill."""
    from risklens import gateway, server as srv
    g = gateway.Gateway()
    g.mark("BTC-USD", 62_000.5, vol=0.45)
    g.set_limits("bench-session", {"max_orders_per_minute": 10**4})
    ts = []
    t_all = time.perf_counter()
    for i in range(n):
        if i % 600 == 0:
            g.mark("BTC-USD", 62_000.5 + i % 7, vol=0.45)        # keep the price fresh
        t0 = time.perf_counter()
        d = g.submit("bench-session", {"symbol": "BTC-USD", "side": "buy" if i % 2 == 0 else "sell",
                                       "qty": "0.01"})
        ts.append((time.perf_counter() - t0) * 1e6)
        assert d["approved"]
    wall = time.perf_counter() - t_all
    d = os.path.join(tmp, "gw-data")
    os.makedirs(d)
    app, httpd = srv.serve("127.0.0.1", 0, d, os.path.join(tmp, "gw-res"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    import http.client
    try:
        app.gateway.mark("BTC-USD", 62_000.5, vol=0.45)
        app.gateway.set_limits("bench-http-1", {"max_orders_per_minute": 10**4})
        port = httpd.server_address[1]
        http_ms = []
        for i in range(n_http):
            if i % 300 == 0:
                app.gateway.mark("BTC-USD", 62_000.5, vol=0.45)
            body = json.dumps({"symbol": "BTC-USD", "side": "buy" if i % 2 == 0 else "sell", "qty": "0.01"})
            t0 = time.perf_counter()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)   # HTTP/1.0 server
            conn.request("POST", "/api/gateway/order", body=body,
                         headers={"Content-Type": "application/json", "X-RiskLens-Session": "bench-http-1"})
            r = conn.getresponse()
            out = json.loads(r.read())
            conn.close()
            http_ms.append((time.perf_counter() - t0) * 1000)
            assert out["decision"]["approved"]
    finally:
        httpd.shutdown()
        httpd.server_close()
    save("gateway.json", {
        "env": env(),
        "workload": f"{n:,} orders (alternating buy/sell 0.01 BTC) through all eight checks and the "
                    f"simulated fill, one paper account; then {n_http:,} orders over HTTP on loopback "
                    f"(a new TCP connection per order, as the server speaks HTTP/1.0; the response includes "
                    f"the full account state)",
        "check_us_in_process": {k: round(v, 1) for k, v in lat(ts).items()},
        "checks_per_second_in_process": round(n / wall),
        "order_round_trip_ms_http": lat(http_ms)})


def main():
    which = sys.argv[1:] or ["engine", "run", "server", "live", "gateway"]
    tmp = tempfile.mkdtemp(prefix="risklens-bench-")
    try:
        if "engine" in which:
            if hasattr(os, "sched_setaffinity"):
                os.sched_setaffinity(0, {0})
            bench_engine()
        if "run" in which:
            bench_run_and_luck(tmp)
        if "server" in which:
            bench_server(tmp)
        if "live" in which:
            bench_live(tmp)
        if "gateway" in which:
            bench_gateway(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
