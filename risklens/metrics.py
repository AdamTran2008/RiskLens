"""Performance and risk metrics, each with one explicit definition.

All metrics use daily close-to-close returns of the equity curve.
Annualization assumes 252 trading days. Sharpe and Sortino use a zero
risk-free rate (stated, not hidden).
"""
from __future__ import annotations

import bisect
import math
import random
from operator import mul
from statistics import NormalDist

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


# ------------------------------------------------- VaR accuracy check
BASEL_GREEN_MAX, BASEL_YELLOW_MAX = 4, 9      # exceptions in 250 days at 99% VaR


def chi2_1_sf(x: float) -> float:
    """P(X > x) for a chi-square variable with 1 degree of freedom."""
    return math.erfc(math.sqrt(max(x, 0.0) / 2))


def _xlogy(c, q):
    """c * log(q) with the convention 0 * log(0) = 0."""
    return c * math.log(q) if c else 0.0


def kupiec_pof(n: int, x: int, p: float) -> tuple[float, float]:
    """Kupiec (1995) proportion-of-failures test. Under a correct model the
    number of VaR exceptions x in n days is Binomial(n, p). Returns the
    likelihood-ratio statistic and its chi-square(1) p-value; a small
    p-value means the exception rate is not consistent with p."""
    if n <= 0:
        return 0.0, 1.0
    q = x / n
    lr = -2 * ((_xlogy(n - x, 1 - p) + _xlogy(x, p)) - (_xlogy(n - x, 1 - q) + _xlogy(x, q)))
    lr = max(lr, 0.0)
    return lr, chi2_1_sf(lr)


def christoffersen_ind(hits: list[bool]) -> tuple[float, float]:
    """Christoffersen (1998) independence test: are exceptions clustered?
    Compares P(exception | exception yesterday) with P(exception | none
    yesterday). A small p-value means exceptions bunch together."""
    n = [[0, 0], [0, 0]]
    for a, b in zip(hits, hits[1:]):
        n[a][b] += 1
    n00, n01, n10, n11 = n[0][0], n[0][1], n[1][0], n[1][1]
    total = n00 + n01 + n10 + n11
    if total == 0 or n01 + n11 == 0 or n00 + n10 == 0:
        return 0.0, 1.0
    pi = (n01 + n11) / total
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    l_indep = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    l_markov = _xlogy(n00, 1 - pi0) + _xlogy(n01, pi0) + _xlogy(n10, 1 - pi1) + _xlogy(n11, pi1)
    lr = max(-2 * (l_indep - l_markov), 0.0)
    return lr, chi2_1_sf(lr)


def basel_zone(exceptions_250: int) -> str:
    return ("green" if exceptions_250 <= BASEL_GREEN_MAX
            else "yellow" if exceptions_250 <= BASEL_YELLOW_MAX else "red")


def rolling_var(returns: list[float], level: float, window: int) -> list[float]:
    """Forecast for day t (t >= window) = historical VaR of returns[t-window:t].
    Uses only days strictly before t, so there is no look-ahead. Same
    nearest-rank definition as var_cvar; the window is kept sorted."""
    k = max(1, math.ceil(round((1 - level) * window, 9)))
    win = sorted(returns[:window])
    out = []
    for t in range(window, len(returns)):
        out.append(-win[k - 1])
        win.pop(bisect.bisect_left(win, returns[t - window]))
        bisect.insort(win, returns[t])
    return out


# ------------------------------------------------------------ VaR models
# Four ways to forecast tomorrow's VaR. Each forecast for day t uses only
# returns before day t (tested for every model). The VaR check then grades
# them on the same days with the same tests.
VAR_MODELS = {
    "historical": "Historical simulation (250 days)",
    "normal": "Normal distribution (250 days)",
    "ewma": "EWMA volatility (RiskMetrics, λ = 0.94)",
    "fhs": "Filtered historical (EWMA + past shocks)",
}
EWMA_LAMBDA = 0.94          # J.P. Morgan RiskMetrics (1996) daily decay
EWMA_SEED_DAYS = 20         # recursion starts here; by day 250 the seed weighs 0.94^230 ~ 1e-6


def _z(level: float) -> float:
    """Standard normal quantile for the loss tail, e.g. -1.645 at 95%."""
    return NormalDist().inv_cdf(1 - level)


def normal_var(returns: list[float], level: float, window: int) -> list[float]:
    """VaR_t = -(mean + z * sd) of returns[t-window:t] (sample sd). Assumes
    returns are normal: no fat tails."""
    z = _z(level)
    out = []
    for t in range(window, len(returns)):
        w = returns[t - window:t]
        m = math.fsum(w) / window
        sd = math.sqrt(math.fsum((x - m) ** 2 for x in w) / (window - 1))
        out.append(-(m + z * sd))
    return out


def ewma_sigma(returns: list[float], lam: float = EWMA_LAMBDA,
               seed_days: int = EWMA_SEED_DAYS) -> list[float | None]:
    """RiskMetrics volatility: sigma2[t] = lam * sigma2[t-1] + (1 - lam) * r[t-1]^2,
    zero mean. sigma[t] is the forecast for day t made with returns up to
    day t-1; None before ``seed_days`` (seeded with their mean square)."""
    n = len(returns)
    out: list[float | None] = [None] * n
    if n <= seed_days:
        return out
    var = math.fsum(r * r for r in returns[:seed_days]) / seed_days
    for t in range(seed_days, n):
        out[t] = math.sqrt(var)
        var = lam * var + (1 - lam) * returns[t] * returns[t]    # include day t only after forecasting it
    return out


def ewma_var(returns: list[float], level: float, window: int, sig=None) -> list[float]:
    """VaR_t = -z * sigma_t: reacts within days when volatility jumps."""
    sig = sig or ewma_sigma(returns)
    z = _z(level)
    return [-z * sig[t] for t in range(window, len(returns))]


def fhs_var(returns: list[float], level: float, window: int, sig=None) -> list[float]:
    """Filtered historical simulation: rescale the past ``window`` days'
    shocks (return / that day's EWMA sigma) to today's sigma, then take the
    historical quantile. Keeps fat tails from history and reacts like EWMA."""
    sig = sig or ewma_sigma(returns)
    first = next((t for t, s in enumerate(sig) if s), None)
    if first is None or first >= window:
        raise ValueError("not enough days to seed EWMA before the first forecast")
    win = sorted(returns[s] / sig[s] for s in range(max(first, 0), window))
    out = []
    for t in range(window, len(returns)):
        k = max(1, math.ceil(round((1 - level) * len(win), 9)))
        out.append(-sig[t] * win[k - 1])
        bisect.insort(win, returns[t] / sig[t])
        old = t - window
        if old >= first:
            win.pop(bisect.bisect_left(win, returns[old] / sig[old]))
    return out


def var_forecasts(returns: list[float], level: float, window: int, model: str,
                  sig=None) -> list[float]:
    if model == "historical":
        return rolling_var(returns, level, window)
    if model == "normal":
        return normal_var(returns, level, window)
    if model == "ewma":
        return ewma_var(returns, level, window, sig)
    if model == "fhs":
        return fhs_var(returns, level, window, sig)
    raise ValueError(f"unknown VaR model {model}")


def chi2_2_sf(x: float) -> float:
    """P(X > x) for chi-square with 2 degrees of freedom."""
    return math.exp(-max(x, 0.0) / 2)


def _basel(hits: list[bool], tdates: list[str]) -> dict | None:
    """Basel traffic light over every 250-test-day window (defined for 99% VaR)."""
    n = len(hits)
    if n < 250:
        return None
    c = sum(hits[:250])
    counts = [c]
    for i in range(250, n):
        c += hits[i] - hits[i - 250]
        counts.append(c)
    zones = [basel_zone(v) for v in counts]
    worst = max(range(len(counts)), key=lambda i: counts[i])
    return {"last_250": counts[-1], "zone": zones[-1], "worst_250": counts[worst],
            "worst_end": tdates[worst + 249],
            "share": {z: zones.count(z) / len(zones) for z in ("green", "yellow", "red")}}


def grade_var(hits: list[bool], level: float, forecasts: list[float], tdates: list[str]) -> dict:
    """The tests a bank must run on its VaR: Kupiec (is the number of
    breaches right?), Christoffersen (are breaches independent, or do they
    cluster?), both together (conditional coverage), and at 99% the Basel
    traffic light."""
    n, x = len(hits), sum(hits)
    p = 1 - level
    k_lr, k_p = kupiec_pof(n, x, p)
    c_lr, c_p = christoffersen_ind(hits)
    rate = x / n if n else 0.0
    verdict = ("accurate" if k_p >= 0.05 else "too optimistic" if rate > p else "too cautious")
    out = {"days": n, "expected": p * n, "exceptions": x, "rate": rate,
           "kupiec_lr": k_lr, "kupiec_p": k_p, "christoffersen_lr": c_lr, "christoffersen_p": c_p,
           "clustered": c_p < 0.05, "cc_lr": k_lr + c_lr, "cc_p": chi2_2_sf(k_lr + c_lr),
           "avg_var": math.fsum(forecasts) / n if n else 0.0,
           "verdict": verdict, "passes": k_p >= 0.05 and c_p >= 0.05}
    if level >= 0.99:
        b = _basel(hits, tdates)
        if b:
            out["basel"] = b
    return out


def var_backtest(returns: list[float], dates: list[str], level: float = 0.95,
                 window: int = 250, keep_series: bool = False, model: str = "historical",
                 sig=None) -> dict | None:
    """Was the VaR right? Each day, forecast VaR from the previous ``window``
    days only, then check whether that day's loss was bigger (an exception).
    A correct 95% VaR is exceeded on about 5% of days, independently.

    ``dates[i]`` is the date of ``returns[i]``. Returns None if there are not
    enough days to test (window + 50)."""
    if len(returns) < window + 50:
        return None
    fc = var_forecasts(returns, level, window, model, sig)
    test = returns[window:]
    tdates = dates[window:]
    hits = [-r > v for r, v in zip(test, fc)]
    out = {"model": model, "level": level, "window": window,
           **grade_var(hits, level, fc, tdates),
           "exception_dates": [d for d, h in zip(tdates, hits) if h]}
    if keep_series:
        out["series"] = {"dates": tdates, "ret": [round(r, 6) for r in test],
                         "var": [round(v, 6) for v in fc]}
    return out


# Which way of measuring risk is right? Two families:
#   account history: VaR from the strategy's own daily P&L (what most
#     backtesters report). When the strategy sits in cash its P&L is zero,
#     so this VaR shrinks, and is far too small the day it buys back in.
#   position x stock: today's exposure (known at today's close: what we hold
#     tonight, or will hold tomorrow if larger) times the stock's own VaR.
#     This is how a risk desk sizes risk: from positions, not past P&L.
RISK_MODELS = {
    "pnl_hist": ("Account history · historical", "pnl", "historical"),
    "pnl_ewma": ("Account history · EWMA", "pnl", "ewma"),
    "pos_hist": ("Position × stock · historical", "pos", "historical"),
    "pos_normal": ("Position × stock · normal", "pos", "normal"),
    "pos_ewma": ("Position × stock · EWMA (RiskMetrics)", "pos", "ewma"),
    "pos_fhs": ("Position × stock · filtered historical", "pos", "fhs"),
}


def compare_risk_models(pnl: list[float], asset: list[float], exposure: list[float],
                        dates: list[str], level: float = 0.95, window: int = 250,
                        keep_series: bool = False) -> dict | None:
    """Grade every risk model on the same days: the days the strategy held
    stock (a day in cash can't lose money, so counting it would flatter
    every model). ``pnl[i]``, ``asset[i]`` are day i's returns of the account
    and of the stock; ``exposure[i]`` is the share of the account at risk on
    day i, known at the previous close; ``dates[i]`` is day i's date."""
    n = len(pnl)
    if n < window + 50 or len(asset) != n or len(exposure) != n:
        return None
    test = [i for i in range(window, n) if exposure[i] > 1e-9]
    if len(test) < 50:
        return None
    sig_pnl, sig_asset = ewma_sigma(pnl), ewma_sigma(asset)
    tdates = [dates[i] for i in test]
    models, full = {}, {}
    for key, (label, base, model) in RISK_MODELS.items():
        if base == "pnl":
            fc = var_forecasts(pnl, level, window, model, sig_pnl)
        else:
            av = var_forecasts(asset, level, window, model, sig_asset)
            fc = [exposure[window + j] * av[j] for j in range(len(av))]
        full[key] = fc
        ft = [fc[i - window] for i in test]
        hits = [-pnl[i] > f for i, f in zip(test, ft)]
        models[key] = {"label": label, "base": base, "model": model, **grade_var(hits, level, ft, tdates)}
    best = max(models, key=lambda k: (models[k]["passes"], models[k]["cc_p"]))
    out = {"level": level, "window": window, "test_days": len(test), "total_days": n - window,
           "models": models, "best": best}
    if keep_series:
        show = ["pnl_hist"] + ([best] if best != "pnl_hist" else [])
        out["series"] = {"dates": dates[window:], "ret": [round(x, 6) for x in pnl[window:]],
                         "invested": [exposure[i] > 1e-9 for i in range(window, n)],
                         "var": {k: [round(v, 6) for v in full[k]] for k in show}}
    return out


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
