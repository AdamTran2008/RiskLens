"""RiskLens benchmarks. Every number in PERFORMANCE.md comes from here.

    python bench/bench.py            # all benchmarks -> bench/results/*.json

1. engine   bars/second vs dataset size (10, 100, 1000 years of daily bars),
            with and without per-day invariant checking; peak memory
2. run      where the time goes in one full 10-year run: load / simulate /
            luck test
3. luck     luck-test time vs number of shuffles
4. server   HTTP latency: cold runs (computed) vs cache hits, 1 and 8
            concurrent clients; server pinned to CPU 0, clients to CPU 1
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


def main():
    which = sys.argv[1:] or ["engine", "run", "server"]
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
