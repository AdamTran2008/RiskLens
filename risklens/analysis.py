"""Deep dive: the questions a risk manager asks after the headline numbers.

  tail_risk         How fat are the tails? Big losses vs what a bell curve predicts.
  rule_attribution  What does each risk rule contribute on its own, and together?
  robustness        Is the result robust or lucky? Nearby settings, and the two
                    halves of the period.
  return_sources    Where do returns come from? Market exposure (beta) vs the
                    rest (alpha), and how much of the market's ups and downs
                    the strategy captures.
  regimes           When does risk show up? Calm, normal and turbulent markets.

Every function is pure: same inputs, same output. The backtest variants used by
rule_attribution and robustness run through the same engine, with every
accounting invariant checked, and are part of the run's content-addressed
result.
"""
from __future__ import annotations

import math
from statistics import NormalDist

from . import engine, metrics, strategies

TRADING_DAYS = metrics.TRADING_DAYS
_N = NormalDist()


def _mean(x):
    return math.fsum(x) / len(x) if x else 0.0


def _sd(x):
    if len(x) < 2:
        return 0.0
    m = _mean(x)
    return math.sqrt(math.fsum((v - m) ** 2 for v in x) / (len(x) - 1))


def _ann_return(rets):
    """Compound annual growth from daily returns."""
    if not rets:
        return 0.0
    g = math.fsum(math.log1p(r) for r in rets) / len(rets)
    return math.expm1(g * TRADING_DAYS)


def _max_dd(rets):
    peak = v = 1.0
    worst = 0.0
    for r in rets:
        v *= 1 + r
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    return worst


def _sharpe(rets):
    sd = _sd(rets)
    return _mean(rets) / sd * math.sqrt(TRADING_DAYS) if sd > 0 else 0.0


def stats(rets):
    """The core numbers for any stream of daily returns."""
    var95, cvar95 = metrics.var_cvar(rets, 0.95)
    return {"cagr": _ann_return(rets), "volatility": _sd(rets) * math.sqrt(TRADING_DAYS),
            "sharpe": _sharpe(rets), "max_drawdown": _max_dd(rets), "var_95": var95,
            "cvar_95": cvar95, "worst_day": -min(rets) if rets else 0.0}


# ------------------------------------------------------------------ tails
def tail_risk(rets: list[float], dates: list[str]) -> dict | None:
    """Compare the real distribution of daily returns with a normal (bell
    curve) one of the same mean and volatility."""
    n = len(rets)
    if n < 60:
        return None
    m, sd = _mean(rets), _sd(rets)
    if sd <= 0:
        return None
    z = [(r - m) / sd for r in rets]
    skew = math.fsum(v ** 3 for v in z) / n
    kurt = math.fsum(v ** 4 for v in z) / n          # normal = 3
    beyond = []
    for k in (2, 3, 4, 5):
        obs = sum(1 for v in z if v < -k)
        exp = n * _N.cdf(-k)
        beyond.append({"sigma": k, "observed": obs, "expected": exp,
                       "ratio": obs / exp if exp > 0 else None})
    i = min(range(n), key=lambda j: rets[j])
    zw = z[i]
    p = _N.cdf(zw)
    years = 1 / (p * TRADING_DAYS) if p > 0 else math.inf
    s = sorted(rets)
    k1 = max(1, n // 100)
    left, right = _mean(s[:k1]), _mean(s[-k1:])
    # histogram in half-sigma bins from -6 to +6 sigma (outliers in the end bins)
    edges = [x / 2 for x in range(-12, 13)]
    counts = [0] * (len(edges) + 1)
    for v in z:
        j = 0
        while j < len(edges) and v >= edges[j]:
            j += 1
        counts[j] += 1
    expected = []
    for j in range(len(edges) + 1):
        lo = -math.inf if j == 0 else edges[j - 1]
        hi = math.inf if j == len(edges) else edges[j]
        expected.append(n * (_N.cdf(hi) - _N.cdf(lo)))
    var99_hist = metrics.var_cvar(rets, 0.99)[0]
    var99_normal = -(m + _N.inv_cdf(0.01) * sd)
    return {
        "days": n, "mean": m, "sd": sd, "skew": skew, "kurtosis": kurt,
        "excess_kurtosis": kurt - 3, "beyond": beyond,
        "worst": {"date": dates[i], "return": rets[i], "sigma": zw, "normal_years": years},
        "tail_ratio": abs(left) / right if right > 0 else None,
        "avg_worst_1pct": left, "avg_best_1pct": right,
        "var99_hist": var99_hist, "var99_normal": var99_normal,
        "hist": {"edges": edges, "counts": counts, "expected": expected},
    }


# ------------------------------------------------------- backtest variants
def _run(bars, cfg, risk_kwargs, params=None):
    st = strategies.make(cfg["strategy"]["name"],
                         cfg["strategy"]["params"] if params is None else params)
    return engine.run(bars, st, initial_cash=round(cfg["initial_cash"] * 100),
                      costs=engine.Costs(**cfg["costs"]), risk=engine.RiskConfig(**risk_kwargs))


def _summary_of(res):
    rets = metrics.daily_returns(res.equity)
    out = stats(rets)
    out["time_in_market"] = sum(1 for p in res.position if p > 0) / len(res.position)
    out["trades"] = len(res.trades)
    out["breaker_trips"] = sum(1 for e in res.risk_events if e[1] == "DRAWDOWN_BREAKER")
    return out


def rule_attribution(bars, cfg) -> dict | None:
    """Run the strategy with no rules, with each rule alone, and with all of
    them, to see what each rule buys on its own."""
    risk = cfg["risk"]
    rules = []
    if risk["vol_target"]:
        rules.append(("vol", "Volatility target only", {"vol_target": risk["vol_target"]}))
    if risk["max_drawdown"]:
        rules.append(("breaker", "Circuit breaker only", {"max_drawdown": risk["max_drawdown"]}))
    if risk["max_exposure"] < 1:
        rules.append(("cap", "Exposure cap only", {"max_exposure": risk["max_exposure"]}))
    if not rules:
        return None
    keep = {"vol_lookback": risk["vol_lookback"], "cooldown_days": risk["cooldown_days"],
            "rebalance_band": risk["rebalance_band"]}
    variants = [("none", "No risk rules", {})] + rules + [("all", "All rules together", {
        "vol_target": risk["vol_target"], "max_drawdown": risk["max_drawdown"],
        "max_exposure": risk["max_exposure"]})]
    out = []
    for key, label, kw in variants:
        res = _run(bars, cfg, {**keep, **kw})
        out.append({"key": key, "label": label, **_summary_of(res)})
    base = out[0]["max_drawdown"]
    for v in out:
        v["dd_cut"] = base - v["max_drawdown"]           # percentage points of drawdown removed
    singles = [v for v in out if v["key"] not in ("none", "all")]
    best = max(singles, key=lambda v: v["dd_cut"]) if singles else None
    allr = out[-1]
    return {"variants": out, "biggest": best["key"] if best else None,
            "interaction": allr["dd_cut"] - sum(v["dd_cut"] for v in singles) if len(singles) > 1 else None}


def _grid(cfg):
    name, p = cfg["strategy"]["name"], cfg["strategy"]["params"]
    if name == "sma_cross":
        f, s = p.get("fast", 50), p.get("slow", 200)
        mult = (0.5, 0.75, 1.0, 1.25, 1.5)
        fs = sorted({max(1, min(400, round(f * m))) for m in mult})
        ss = sorted({max(2, min(1000, round(s * m))) for m in mult})
        return "sma_cross", {"fast": fs, "slow": ss}, [
            {"fast": a, "slow": b} for a in fs for b in ss if a < b]
    if name == "momentum":
        L = p.get("lookback", 126)
        ls = sorted({max(1, min(1000, round(L * m))) for m in (0.33, 0.5, 0.67, 0.83, 1.0, 1.25, 1.5, 2.0)})
        return "momentum", {"lookback": ls}, [{"lookback": x} for x in ls]
    return name, {}, []


def _halves(rets, dates):
    h = len(rets) // 2
    return [{"from": dates[0], "to": dates[h - 1], **stats(rets[:h])},
            {"from": dates[h], "to": dates[-1], **stats(rets[h:])}]


def robustness(bars, cfg, res) -> dict:
    """Would nearby settings have done about as well (robust), or only the
    chosen one (lucky)? And did it hold up in both halves of the period?"""
    name, axes, combos = _grid(cfg)
    risk = cfg["risk"]
    bh = engine.run(bars, strategies.make("buy_and_hold", {}),
                    initial_cash=round(cfg["initial_cash"] * 100), costs=engine.Costs(**cfg["costs"]))
    bh_rets = metrics.daily_returns(bh.equity)
    bh_stats = stats(bh_rets)
    grid = []
    for params in combos:
        r = _run(bars, cfg, risk, params)
        st = stats(metrics.daily_returns(r.equity))
        grid.append({"params": params, "sharpe": st["sharpe"], "max_drawdown": st["max_drawdown"],
                     "cagr": st["cagr"]})
    chosen = cfg["strategy"]["params"]
    out = {"strategy": name, "axes": axes, "grid": grid, "buy_and_hold": bh_stats}
    if grid:
        sh = sorted((g["sharpe"] for g in grid), reverse=True)
        mine = next((g for g in grid if g["params"] == chosen), None)
        out.update({
            "n": len(grid),
            "beat_bh_sharpe": sum(1 for g in grid if g["sharpe"] > bh_stats["sharpe"]),
            "beat_bh_drawdown": sum(1 for g in grid if g["max_drawdown"] < bh_stats["max_drawdown"]),
            "sharpe_min": sh[-1], "sharpe_max": sh[0], "sharpe_median": sh[len(sh) // 2],
            "chosen_rank": 1 + sh.index(mine["sharpe"]) if mine else None,
        })
    # the same run with trading costs 3x and 10x higher: does the result survive worse fills?
    c = cfg["costs"]
    out["costs"] = []
    for mult in (1, 3, 10):
        cc = dict(cfg, costs={"fee_bps": c["fee_bps"] * mult, "slippage_bps": c["slippage_bps"] * mult})
        r = _run(bars, cc, risk)
        st = stats(metrics.daily_returns(r.equity))
        out["costs"].append({"mult": mult, "fee_bps": c["fee_bps"] * mult, "slippage_bps": c["slippage_bps"] * mult,
                             "sharpe": st["sharpe"], "max_drawdown": st["max_drawdown"], "cagr": st["cagr"],
                             "trades": len(r.trades), "fees": r.fees_paid / 100})
    rets = metrics.daily_returns(res.equity)
    d = res.dates[1:]
    if len(rets) >= 120:
        out["halves"] = _halves(rets, d)
        out["halves_bh"] = _halves(bh_rets, d)
    return out


# ---------------------------------------------------------- return sources
def return_sources(strat: list[float], asset: list[float], exposure: list[float]) -> dict | None:
    """Regress the strategy's daily returns on the stock's: beta is market
    exposure, alpha what is left over (annualized, with its t-statistic)."""
    n = len(strat)
    if n < 60 or len(asset) != n:
        return None
    ma, ms = _mean(asset), _mean(strat)
    sxx = math.fsum((a - ma) ** 2 for a in asset)
    if sxx <= 0:
        return None
    sxy = math.fsum((a - ma) * (s - ms) for a, s in zip(asset, strat))
    beta = sxy / sxx
    alpha = ms - beta * ma
    resid = [s - alpha - beta * a for a, s in zip(asset, strat)]
    s2 = math.fsum(e * e for e in resid) / (n - 2)
    se_alpha = math.sqrt(s2 * (1 / n + ma * ma / sxx))
    syy = math.fsum((s - ms) ** 2 for s in strat)
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    up = [(s, a) for s, a in zip(strat, asset) if a > 0]
    dn = [(s, a) for s, a in zip(strat, asset) if a < 0]

    def capture(pairs):
        """Average strategy return / average stock return on those days."""
        if not pairs:
            return None
        ma_ = _mean([a for _, a in pairs])
        return _mean([s for s, _ in pairs]) / ma_ if ma_ else None
    return {
        "beta": beta, "alpha_annual": alpha * TRADING_DAYS,
        "alpha_t": alpha / se_alpha if se_alpha > 0 else 0.0, "r2": r2,
        "up_capture": capture(up), "down_capture": capture(dn),
        "avg_exposure": _mean(exposure) if exposure else None,
        "strategy_cagr": _ann_return(strat), "asset_cagr": _ann_return(asset),
    }


# ------------------------------------------------------------------ regimes
REGIME_LOOKBACK = 20


def regime_labels(asset: list[float]) -> tuple[list[str | None], dict]:
    """Label each day calm / normal / turbulent by the stock's volatility over
    the previous 20 days (known before the day starts). Cut-offs are the
    thirds of that volatility over the whole period."""
    n = len(asset)
    vol: list[float | None] = [None] * n
    for t in range(REGIME_LOOKBACK, n):
        vol[t] = _sd(asset[t - REGIME_LOOKBACK:t]) * math.sqrt(TRADING_DAYS)
    known = sorted(v for v in vol if v is not None)
    if len(known) < 30:
        return [None] * n, {}
    lo, hi = known[len(known) // 3], known[2 * len(known) // 3]
    labels = [None if v is None else "calm" if v < lo else "turbulent" if v >= hi else "normal"
              for v in vol]
    return labels, {"calm_below": lo, "turbulent_from": hi}


def regimes(strat: list[float], asset: list[float], exposure: list[float]) -> dict | None:
    labels, cut = regime_labels(asset)
    if not cut:
        return None
    out = {"cutoffs": cut, "regimes": []}
    total = sum(1 for x in labels if x)
    for name in ("calm", "normal", "turbulent"):
        idx = [i for i, x in enumerate(labels) if x == name]
        if not idx:
            continue
        s = [strat[i] for i in idx]
        a = [asset[i] for i in idx]
        out["regimes"].append({
            "name": name, "days": len(idx), "share": len(idx) / total,
            "asset": {"ann_return": _mean(a) * TRADING_DAYS, "volatility": _sd(a) * math.sqrt(TRADING_DAYS),
                      "worst_day": -min(a)},
            "strategy": {"ann_return": _mean(s) * TRADING_DAYS, "volatility": _sd(s) * math.sqrt(TRADING_DAYS),
                         "worst_day": -min(s), "var_95": metrics.var_cvar(s, 0.95)[0]},
            "avg_exposure": _mean([exposure[i] for i in idx]),
        })
    return out
