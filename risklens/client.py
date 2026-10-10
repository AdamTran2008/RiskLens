"""RiskLens command-line client. Talks to the run server over HTTP.

    python -m risklens.client datasets
    python -m risklens.client compare AAPL --strategy sma_cross --fast 50 --slow 200 \
        --vol-target 0.15 --max-drawdown 0.15
    python -m risklens.client run AAPL --strategy momentum --lookback 126
    python -m risklens.client show <run_id>

``compare`` submits the same strategy twice, without and with risk rules,
prints a side-by-side table and saves an HTML report.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import urllib.error
import urllib.request
import webbrowser


class Client:
    def __init__(self, base="http://127.0.0.1:8765"):
        self.base = base.rstrip("/")

    def _req(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except Exception:
                msg = str(e)
            raise SystemExit(f"server error {e.code}: {msg}")
        except urllib.error.URLError as e:
            raise SystemExit(f"cannot reach RiskLens server at {self.base} ({e.reason}). "
                             f"Start it with: python -m risklens.server")
        return payload if raw else json.loads(payload)

    def datasets(self):
        return self._req("GET", "/datasets")

    def submit(self, cfg):
        return self._req("POST", "/runs", cfg)

    def get(self, rid):
        return self._req("GET", f"/runs/{rid}")

    def report(self, ids):
        return self._req("GET", "/report?runs=" + ",".join(ids), raw=True)


def build_config(a, with_risk: bool) -> dict:
    params = {}
    if a.strategy == "sma_cross":
        params = {"fast": a.fast, "slow": a.slow}
    elif a.strategy == "momentum":
        params = {"lookback": a.lookback}
    cfg = {"symbol": a.symbol, "start": a.start, "end": a.end,
           "strategy": {"name": a.strategy, "params": params},
           "costs": {"fee_bps": a.fee_bps, "slippage_bps": a.slippage_bps},
           "luck_test": {"trials": a.trials, "seed": 7}}
    if with_risk:
        cfg["risk"] = {"max_exposure": a.max_exposure, "vol_target": a.vol_target,
                       "max_drawdown": a.max_drawdown, "cooldown_days": a.cooldown}
    return cfg


ROWS = [("Total return", "total_return", "pct"), ("CAGR", "cagr", "pct"),
        ("Volatility", "volatility", "pct"), ("Sharpe", "sharpe", "num"),
        ("Max drawdown", "max_drawdown", "negpct"), ("1-day VaR 95%", "var_95", "negpct"),
        ("1-day CVaR 95%", "cvar_95", "negpct"), ("Worst day", "worst_day", "negpct"),
        ("Time in market", "time_in_market", "pct"), ("Trades", "trades", "int")]


def fmt(v, kind):
    if v is None:
        return "-"
    if kind == "pct":
        return f"{v*100:6.1f}%"
    if kind == "negpct":
        return f"{-v*100:6.1f}%"
    if kind == "num":
        return f"{v:6.2f}"
    return f"{int(v):6d}"


def print_table(results, names):
    w = 22
    print(f"{'':<16}" + "".join(f"{n:>{w}}" for n in names))
    for label, key, kind in ROWS:
        print(f"{label:<16}" + "".join(f"{fmt(r['summary'][key], kind):>{w}}" for r in results))
    print(f"{'Luck p-value':<16}" + "".join(
        f"{(format(r['luck_test']['p_value'], '.3f') if r['luck_test'] else '-'):>{w}}" for r in results))


def main(argv=None):
    if hasattr(signal, "SIGPIPE"):          # behave like a normal CLI when piped to head
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(description="RiskLens client")
    ap.add_argument("--server", default=os.environ.get("RISKLENS_SERVER", "http://127.0.0.1:8765"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("datasets")
    sh = sub.add_parser("show")
    sh.add_argument("run_id")
    for name in ("run", "compare"):
        p = sub.add_parser(name)
        p.add_argument("symbol")
        p.add_argument("--strategy", default="sma_cross",
                       choices=["sma_cross", "momentum", "buy_and_hold"])
        p.add_argument("--fast", type=int, default=50)
        p.add_argument("--slow", type=int, default=200)
        p.add_argument("--lookback", type=int, default=126)
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--fee-bps", type=float, default=1.0)
        p.add_argument("--slippage-bps", type=float, default=2.0)
        p.add_argument("--max-exposure", type=float, default=1.0)
        p.add_argument("--vol-target", type=float, default=0.15)
        p.add_argument("--max-drawdown", type=float, default=0.15)
        p.add_argument("--cooldown", type=int, default=20)
        p.add_argument("--trials", type=int, default=1000, help="luck-test shuffles (0 = off)")
        p.add_argument("--no-risk", action="store_true", help="(run only) disable risk rules")
        p.add_argument("--out", default="reports", help="folder for the HTML report")
        p.add_argument("--open", action="store_true", help="open the report in a browser")
    a = ap.parse_args(argv)
    c = Client(a.server)

    if a.cmd == "datasets":
        for sym, e in c.datasets().items():
            print(f"{sym:<8} {e['rows']:>6} bars  {e['start']} .. {e['end']}  "
                  f"sha256={e['sha256'][:12]}  source={e['source']}")
        return
    if a.cmd == "show":
        r = c.get(a.run_id)
        print(json.dumps({k: r[k] for k in ("run_id", "config", "manifest", "summary",
                                            "luck_test")}, indent=2))
        return

    if a.cmd == "run":
        cfgs = [build_config(a, with_risk=not a.no_risk)]
        names = ["result"]
    else:
        cfgs = [build_config(a, False), build_config(a, True)]
        names = ["no risk rules", "with risk rules"]
    results = []
    for cfg in cfgs:
        r = c.submit(cfg)
        results.append(r)
        print(f"run {r['run_id']}  {'(cached)' if r['cached'] else '(computed)'}  "
              f"{r['server_ms']:.0f} ms  data={r['manifest']['dataset_sha256'][:12]}")
    print()
    print_table(results, names)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{a.symbol}_{'_'.join(r['run_id'][:8] for r in results)}.html")
    with open(path, "wb") as f:
        f.write(c.report([r["run_id"] for r in results]))
    print(f"\nreport: {os.path.abspath(path)}")
    if a.open:
        webbrowser.open("file://" + os.path.abspath(path))


if __name__ == "__main__":
    main()
