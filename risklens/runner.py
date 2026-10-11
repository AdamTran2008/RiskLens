"""Run configs: strict validation, canonical form, content-addressed IDs.

A run is fully determined by (normalized config, dataset SHA-256, engine
version). Its ID is the SHA-256 of those three, so:
  * submitting the same run twice returns the same ID (idempotent), and
  * any change to config, data or engine code produces a different ID, so a
    stored result can never be silently stale.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time

from . import analysis, engine, metrics, strategies
from .data import load_csv, sha256_file, slice_dates

_CORE = ["data.py", "engine.py", "metrics.py", "strategies.py", "runner.py", "analysis.py"]


def engine_version() -> str:
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in _CORE:
        with open(os.path.join(here, name), "rb") as f:
            h.update(name.encode() + b"\0" + f.read())
    return h.hexdigest()[:16]


class ConfigError(ValueError):
    pass


def _num(d, k, lo, hi, default, allow_none=False, integer=False):
    v = d.get(k, default)
    if v is None and allow_none:
        return None
    ok = (type(v) is int) if integer else (type(v) in (int, float))
    if not ok or (not integer and not math.isfinite(v)) or not lo <= v <= hi:
        kind = "an integer" if integer else "a number"
        raise ConfigError(f"{k} must be {kind} in [{lo}, {hi}]" + (" or null" if allow_none else ""))
    return int(v) if integer else float(v)


def _keys(d, allowed, where):
    if type(d) is not dict:
        raise ConfigError(f"{where} must be an object")
    extra = set(d) - set(allowed)
    if extra:
        raise ConfigError(f"{where}: unknown field(s) {sorted(extra)}")


DATE_LEN = 10
SYMBOL_RE = re.compile(r"[A-Z0-9.\-=^]{1,15}")       # e.g. AAPL, BRK-B, ^GSPC, EURUSD=X


def normalize(cfg: dict) -> dict:
    """Validate a user config and fill defaults. Unknown fields are errors."""
    _keys(cfg, {"symbol", "start", "end", "strategy", "risk", "costs",
                "initial_cash", "luck_test"}, "config")
    sym = cfg.get("symbol")
    if type(sym) is not str or not SYMBOL_RE.fullmatch(sym.upper()):
        raise ConfigError("symbol must be 1-15 letters, digits or . - = ^")
    out = {"symbol": sym.upper()}
    for k in ("start", "end"):
        v = cfg.get(k)
        if v is not None and (type(v) is not str or len(v) != DATE_LEN or v[4] != "-" or v[7] != "-"):
            raise ConfigError(f"{k} must be YYYY-MM-DD or null")
        out[k] = v
    if out["start"] and out["end"] and out["start"] > out["end"]:
        raise ConfigError("start is after end")

    st = cfg.get("strategy", {"name": "buy_and_hold"})
    _keys(st, {"name", "params"}, "strategy")
    name = st.get("name")
    params = st.get("params", {})
    if type(params) is not dict:
        raise ConfigError("strategy.params must be an object")
    try:
        strategies.make(name, params)
    except (ValueError, TypeError) as e:
        raise ConfigError(str(e))
    out["strategy"] = {"name": name, "params": dict(sorted(params.items()))}

    rk = cfg.get("risk", {})
    _keys(rk, {"max_exposure", "vol_target", "vol_lookback", "max_drawdown",
               "cooldown_days", "rebalance_band"}, "risk")
    d = engine.RiskConfig()
    out["risk"] = {
        "max_exposure": _num(rk, "max_exposure", 0.01, 1.0, d.max_exposure),
        "vol_target": _num(rk, "vol_target", 0.01, 2.0, d.vol_target, allow_none=True),
        "vol_lookback": _num(rk, "vol_lookback", 2, 252, d.vol_lookback, integer=True),
        "max_drawdown": _num(rk, "max_drawdown", 0.01, 0.99, d.max_drawdown, allow_none=True),
        "cooldown_days": _num(rk, "cooldown_days", 0, 252, d.cooldown_days, integer=True),
        "rebalance_band": _num(rk, "rebalance_band", 0.0, 0.5, d.rebalance_band),
    }
    cs = cfg.get("costs", {})
    _keys(cs, {"fee_bps", "slippage_bps"}, "costs")
    dc = engine.Costs()
    out["costs"] = {"fee_bps": _num(cs, "fee_bps", 0, 100, dc.fee_bps),
                    "slippage_bps": _num(cs, "slippage_bps", 0, 100, dc.slippage_bps)}
    out["initial_cash"] = _num(cfg, "initial_cash", 1_000, 1e9, 100_000)
    lt = cfg.get("luck_test", {})
    _keys(lt, {"trials", "seed"}, "luck_test")
    out["luck_test"] = {"trials": _num(lt, "trials", 0, 20_000, 1000, integer=True),
                        "seed": _num(lt, "seed", 0, 2**31, 7, integer=True)}
    return out


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def run_id(cfg: dict, dataset_sha: str, version: str) -> str:
    return hashlib.sha256(canonical({"config": cfg, "data": dataset_sha,
                                     "engine": version}).encode()).hexdigest()[:24]


def execute(cfg: dict, data_path: str, dataset_sha: str | None = None,
            version: str | None = None) -> dict:
    """Run a normalized config against a dataset file. Returns the full,
    JSON-serializable result including its manifest."""
    t0 = time.perf_counter()
    dataset_sha = dataset_sha or sha256_file(data_path)
    version = version or engine_version()
    bars = slice_dates(load_csv(data_path), cfg["start"], cfg["end"])
    if len(bars) < 3:
        raise ConfigError("fewer than 3 bars in the requested date range")
    t_load = time.perf_counter() - t0
    strat = strategies.make(cfg["strategy"]["name"], cfg["strategy"]["params"])
    t1 = time.perf_counter()
    res = engine.run(bars, strat, initial_cash=round(cfg["initial_cash"] * 100),
                     costs=engine.Costs(**cfg["costs"]),
                     risk=engine.RiskConfig(**cfg["risk"]))
    t_sim = time.perf_counter() - t1
    summ = metrics.summary(res.equity, res.dates, res.position, res.trades, res.fees_paid)
    pnl = metrics.daily_returns(res.equity)
    asset = [res.close[i + 1] / res.close[i] - 1 for i in range(len(res.close) - 1)]
    # share of the account at risk on day i+1, known at day i's close: what we
    # hold tonight, or what we will hold tomorrow if that is larger
    exposure = [max(res.position[i] * res.close[i] / res.equity[i], res.target[i])
                if res.equity[i] > 0 else 0.0 for i in range(len(res.equity) - 1)]
    risk_models = {"95": metrics.compare_risk_models(pnl, asset, exposure, res.dates[1:], 0.95,
                                                     keep_series=True),
                   "99": metrics.compare_risk_models(pnl, asset, exposure, res.dates[1:], 0.99)}
    t_deep = time.perf_counter()
    has_rules = bool(cfg["risk"]["vol_target"] or cfg["risk"]["max_drawdown"]
                     or cfg["risk"]["max_exposure"] < 1)
    deep = {
        "tails": analysis.tail_risk(asset, res.dates[1:]),
        "attribution": analysis.rule_attribution(bars, cfg) if has_rules else None,
        "robustness": analysis.robustness(bars, cfg, res),
        "sources": analysis.return_sources(pnl, asset, exposure),
        "regimes": analysis.regimes(pnl, asset, exposure),
    }
    t_deep = time.perf_counter() - t_deep
    luck = None
    t2 = time.perf_counter()
    if cfg["luck_test"]["trials"]:
        asset = [res.close[i] / res.close[i - 1] - 1 for i in range(1, len(res.close))]
        exposure = res.target[:-1]       # decided at close t-1, held over day t
        luck = metrics.luck_test(asset, exposure, cfg["luck_test"]["trials"],
                                 cfg["luck_test"]["seed"])
    return {
        "run_id": run_id(cfg, dataset_sha, version),
        "config": cfg,
        "manifest": {"dataset_sha256": dataset_sha, "engine_version": version,
                     "bars": len(bars), "result_fingerprint": res.fingerprint()},
        "summary": summ,
        "luck_test": luck,
        "risk_models": risk_models,
        "deep": deep,
        "risk_events": res.risk_events,
        "trades": res.trades,
        "series": {"dates": res.dates, "equity": res.equity, "position": res.position,
                   "close": res.close, "target": res.target,
                   "drawdown": [round(x, 6) for x in metrics.drawdown_series(res.equity)]},
        "timing_ms": {"load": round(t_load * 1000, 2), "simulate": round(t_sim * 1000, 2),
                      "deep_dive": round(t_deep * 1000, 2),
                      "luck_test": round((time.perf_counter() - t2) * 1000, 2),
                      "total": round((time.perf_counter() - t0) * 1000, 2)},
    }
