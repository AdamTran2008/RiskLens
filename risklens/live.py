"""Live risk monitor: replay history through the engine one day at a time.

The engine runs for real, day by day, and after each simulated day the
monitor emits a snapshot: account value and drawdown with and without risk
rules, how much is invested, rolling 20-day volatility, rolling 1-day VaR,
and any risk-rule events that fired that day. The server streams these to the
browser with Server-Sent Events so the dashboard updates like a risk desk.

The replay is deterministic: same config and data give the same stream. Pause
or speed changes are done by reconnecting with ``start=<day>``; the engine
re-runs silently up to that day (it is fast) and resumes emitting.
"""
from __future__ import annotations

import math
import time

from . import engine, metrics, strategies
from .data import load_csv, slice_dates

VOL_WINDOW = 20
VAR_WINDOW = 250


class ClientGone(Exception):
    """Raised by an emit function when the viewer disconnected."""


def _rolling_vol(rets):
    w = rets[-VOL_WINDOW:]
    if len(w) < VOL_WINDOW:
        return None
    m = sum(w) / len(w)
    return math.sqrt(sum((r - m) ** 2 for r in w) / (len(w) - 1)) * math.sqrt(metrics.TRADING_DAYS)


def _rolling_var(rets):
    w = rets[-VAR_WINDOW:]
    if len(w) < VOL_WINDOW:
        return None
    return metrics.var_cvar(w, 0.95)[0]


def _r(x, n=6):
    return None if x is None else round(x, n)


def stream(cfg: dict, data_path: str, emit, start: int = 0, speed: float = 60.0,
           sleep=time.sleep) -> None:
    """Replay ``cfg`` (a normalized run config) and call ``emit(kind, payload)``.

    kind is "init", "day" or "end". ``speed`` is simulated trading days per
    second of wall time. Days before ``start`` are simulated but not emitted.
    """
    bars = slice_dates(load_csv(data_path), cfg["start"], cfg["end"])
    if len(bars) < 3:
        raise ValueError("fewer than 3 bars in the requested date range")
    cash = round(cfg["initial_cash"] * 100)
    costs = engine.Costs(**cfg["costs"])
    rules = engine.RiskConfig(**cfg["risk"])
    plain = engine.RiskConfig(rebalance_band=rules.rebalance_band)   # same strategy, no rules
    name, params = cfg["strategy"]["name"], cfg["strategy"]["params"]

    base = engine.run(bars, strategies.make(name, params), cash, costs, plain)
    base_dd = metrics.drawdown_series(base.equity)
    start = max(0, min(int(start), len(bars) - 1))
    delay = 1.0 / max(1.0, float(speed))

    emit("init", {"symbol": cfg["symbol"], "days": len(bars), "first": bars[0].date,
                  "last": bars[-1].date, "start": start, "initial": cfg["initial_cash"],
                  "strategy": name, "params": params, "risk": cfg["risk"]})

    rets_m, rets_b = [], []
    peak = [cash]

    def on_day(t, bar, res, new_events):
        eq = res.equity[-1]
        if t > 0:
            rets_m.append(eq / res.equity[-2] - 1)
            rets_b.append(base.equity[t] / base.equity[t - 1] - 1)
        peak[0] = max(peak[0], eq)
        if t < start:
            return
        c = res.close[-1]
        payload = {
            "t": t, "d": bar.date, "px": round(bar.close, 4),
            "eq": [base.equity[t] / 100, eq / 100],
            "dd": [_r(base_dd[t]), _r(1 - eq / peak[0])],
            "x": [_r(base.position[t] * c / base.equity[t] if base.equity[t] else 0, 4),
                  _r(res.position[-1] * c / eq if eq else 0, 4)],
            "vol": [_r(_rolling_vol(rets_b), 4), _r(_rolling_vol(rets_m), 4)],
            "var": [_r(_rolling_var(rets_b), 5), _r(_rolling_var(rets_m), 5)],
            "ev": [[k, d] for _, k, d in new_events if k != "CASH_CLIP"],
        }
        emit("day", payload)
        sleep(delay)

    managed = engine.run(bars, strategies.make(name, params), cash, costs, rules, on_day=on_day)
    sb = metrics.summary(base.equity, base.dates, base.position, base.trades, base.fees_paid)
    sm = metrics.summary(managed.equity, managed.dates, managed.position, managed.trades,
                         managed.fees_paid)
    keys = ("total_return", "cagr", "volatility", "sharpe", "max_drawdown", "var_95", "trades")
    emit("end", {"base": {k: sb[k] for k in keys}, "managed": {k: sm[k] for k in keys}})
