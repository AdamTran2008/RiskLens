"""Performance and risk metrics, each with one explicit definition.

All metrics use daily close-to-close returns of the equity curve.
Annualization assumes 252 trading days. Sharpe and Sortino use a zero
risk-free rate (stated, not hidden).
"""
from __future__ import annotations

import math
import random
from operator import mul

TRADING_DAYS = 252


def daily_returns(equity: list[int]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]


def _mean(x):
    return sum(x) / len(x) if x else 0.0


def _std(x):
    """Sample standard deviation (n-1)."""
    if len(x) < 2:
        return 0.0
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def max_drawdown(equity: list[int], dates: list[str] | None = None) -> dict:
    """Largest peak-to-trough fall, with the dates and the days to recover
    (None if never recovered within the data)."""
    peak, peak_i = equity[0], 0
    worst, w_peak, w_trough = 0.0, 0, 0
    for i, e in enumerate(equity):
        if e > peak:
            peak, peak_i = e, i
        dd = 1 - e / peak
        if dd > worst:
            worst, w_peak, w_trough = dd, peak_i, i
    recovered = None
    for j in range(w_trough, len(equity)):
        if equity[j] >= equity[w_peak]:
            recovered = j
            break
    out = {"max_drawdown": worst,
           "peak_index": w_peak, "trough_index": w_trough,
           "recovery_days": (recovered - w_trough) if recovered is not None and worst > 0 else None}
    if dates:
        out["peak_date"] = dates[w_peak]
        out["trough_date"] = dates[w_trough]
    return out


def drawdown_series(equity: list[int]) -> list[float]:
    peak = equity[0]
    out = []
    for e in equity:
        peak = max(peak, e)
        out.append(1 - e / peak)
    return out


def var_cvar(returns: list[float], level: float = 0.95) -> tuple[float, float]:
    """Historical 1-day Value-at-Risk and Conditional VaR (expected shortfall),
    reported as positive loss fractions.

    VaR uses the nearest-rank method: sort returns ascending and take the
    k-th smallest with k = ceil((1-level) * n). CVaR is the mean of the k
    worst returns. Example: n=1000, level=0.95 -> k=50, VaR is the 50th
    worst day and CVaR the average of the 50 worst days."""
    if not returns:
        return 0.0, 0.0
    s = sorted(returns)
    # round before ceil: (1 - 0.95) * 40 is 2.0000000000000018 in floating
    # point, and a bare ceil would wrongly pick the 3rd-worst day (a bug our
    # hand-calculated test caught)
    k = max(1, math.ceil(round((1 - level) * len(s), 9)))
    var = -s[k - 1]
    cvar = -_mean(s[:k])
    return var, cvar


def summary(equity: list[int], dates: list[str], position: list[int],
            trades: list, fees_paid: int) -> dict:
    rets = daily_returns(equity)
    n_years = len(rets) / TRADING_DAYS
    total = equity[-1] / equity[0] - 1
    cagr = (equity[-1] / equity[0]) ** (1 / n_years) - 1 if n_years > 0 and equity[-1] > 0 else 0.0
    vol = _std(rets) * math.sqrt(TRADING_DAYS)
    mean = _mean(rets)
    sd = _std(rets)
    sharpe = mean / sd * math.sqrt(TRADING_DAYS) if sd > 0 else 0.0
    downside = [min(r, 0.0) for r in rets]
    dsd = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0
    sortino = mean / dsd * math.sqrt(TRADING_DAYS) if dsd > 0 else 0.0
    dd = max_drawdown(equity, dates)
    var95, cvar95 = var_cvar(rets, 0.95)
    worst = min(rets) if rets else 0.0
    return {
        "start": dates[0], "end": dates[-1], "days": len(equity),
        "final_equity": equity[-1] / 100,
        "total_return": total,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": dd["max_drawdown"],
        "max_drawdown_peak": dd.get("peak_date"),
        "max_drawdown_trough": dd.get("trough_date"),
        "drawdown_recovery_days": dd["recovery_days"],
        "var_95": var95,
        "cvar_95": cvar95,
        "worst_day": -worst,
        "time_in_market": sum(1 for p in position if p > 0) / len(position),
        "trades": len(trades),
        "fees_paid": fees_paid / 100,
    }


def luck_test(asset_returns: list[float], exposure: list[float], trials: int = 1000,
              seed: int = 7) -> dict:
    """Is the strategy's timing better than luck?

    ``exposure[i]`` is the fraction of equity held during day i (decided the
    day before). We shuffle that exposure sequence across days, which keeps
    *how much* the strategy was invested but destroys *when*. Each shuffle is
    a random strategy with the same appetite for risk. If the real Sharpe
    beats almost all shuffles, the timing carries information; if not, the
    result is consistent with luck.

    p_value = (1 + #shuffles with Sharpe >= actual) / (trials + 1).
    Costs are ignored on both sides so the comparison is like for like.
    """
    n = len(asset_returns)
    if n < 2 or len(exposure) != n:
        raise ValueError("exposure and asset_returns must have the same length >= 2")
    a2 = [a * a for a in asset_returns]

    def sharpe(e, e2):
        # Sharpe of daily returns r_i = e_i * a_i, via sums (fast, C-level map)
        s1 = sum(map(mul, e, asset_returns))
        s2 = sum(map(mul, e2, a2))
        var = (s2 - s1 * s1 / n) / (n - 1)
        if var <= 1e-18:
            return 0.0
        return (s1 / n) / math.sqrt(var) * math.sqrt(TRADING_DAYS)

    exp = list(exposure)
    actual = sharpe(exp, [x * x for x in exp])
    rng = random.Random(seed)
    beats = 0
    dist = []
    for _ in range(trials):
        rng.shuffle(exp)
        s = sharpe(exp, [x * x for x in exp])
        dist.append(s)
        if s >= actual - 1e-12:
            beats += 1
    dist.sort()
    return {
        "trials": trials, "seed": seed,
        "actual_sharpe_gross": actual,
        "random_median_sharpe": dist[len(dist) // 2],
        "random_95th_sharpe": dist[min(len(dist) - 1, int(0.95 * len(dist)))],
        "p_value": (1 + beats) / (trials + 1),
    }
