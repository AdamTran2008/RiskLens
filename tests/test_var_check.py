"""VaR accuracy check: the statistics are hand-checked, the rolling forecast
never uses the day it is forecasting, and the verdicts react the right way to
a model that is right, one that underestimates risk, and clustered breaches."""
import math
import random

import pytest
from hypothesis import given, settings, strategies as st

from risklens import metrics, report, runner


def test_chi2_tail_matches_known_critical_values():
    assert metrics.chi2_1_sf(3.841458820694124) == pytest.approx(0.05, abs=1e-9)
    assert metrics.chi2_1_sf(6.634896601021214) == pytest.approx(0.01, abs=1e-9)
    assert metrics.chi2_1_sf(0) == 1.0


def test_kupiec_by_hand():
    # 5 breaches in 250 days for a 99% VaR (2.5 expected)
    lr, p = metrics.kupiec_pof(250, 5, 0.01)
    want = -2 * ((245 * math.log(0.99) + 5 * math.log(0.01)) - (245 * math.log(0.98) + 5 * math.log(0.02)))
    assert lr == pytest.approx(want) and p == pytest.approx(math.erfc(math.sqrt(want / 2)))
    # zero breaches: LR = -2 n ln(1-p)
    assert metrics.kupiec_pof(250, 0, 0.01)[0] == pytest.approx(-2 * 250 * math.log(0.99))
    # exactly the expected count: perfect fit
    assert metrics.kupiec_pof(1000, 50, 0.05) == pytest.approx((0.0, 1.0))


def test_christoffersen_detects_clusters():
    rng = random.Random(1)
    spread = [rng.random() < 0.05 for _ in range(2000)]   # independent breaches
    bunched = [False] * 1900 + [True] * 100               # same rate, all in a row
    assert metrics.christoffersen_ind(spread)[1] > 0.05
    assert metrics.christoffersen_ind(bunched)[1] < 1e-6
    assert metrics.christoffersen_ind([False] * 100) == (0.0, 1.0)


@pytest.mark.parametrize("x,zone", [(0, "green"), (4, "green"), (5, "yellow"), (9, "yellow"),
                                    (10, "red"), (30, "red")])
def test_basel_traffic_light(x, zone):
    assert metrics.basel_zone(x) == zone


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(-0.2, 0.2, allow_nan=False), min_size=40, max_size=120),
       st.sampled_from([0.9, 0.95, 0.99]), st.integers(5, 30))
def test_rolling_var_equals_var_of_previous_window(rets, level, window):
    fc = metrics.rolling_var(rets, level, window)
    assert len(fc) == len(rets) - window
    for i, v in enumerate(fc):
        t = window + i
        assert v == metrics.var_cvar(rets[t - window:t], level)[0]


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(-0.2, 0.2, allow_nan=False), min_size=40, max_size=120),
       st.integers(10, 39), st.floats(-0.5, 0.5, allow_nan=False))
def test_forecast_never_sees_the_day_it_forecasts(rets, t, new):
    """Changing day t's return (and everything after) cannot change the
    forecasts for days up to and including t."""
    window = 10
    a = metrics.rolling_var(rets, 0.95, window)
    changed = rets[:t] + [new] + [x * -1.7 for x in rets[t + 1:]]
    b = metrics.rolling_var(changed, 0.95, window)
    assert a[:t - window + 1] == b[:t - window + 1]


def dates(n):
    return [f"d{i:05d}" for i in range(n)]


def test_right_model_is_accurate_wrong_model_is_flagged():
    rng = random.Random(3)
    calm = [rng.gauss(0, 0.01) for _ in range(3000)]
    ok = metrics.var_backtest(calm, dates(3000), 0.95)
    assert ok["verdict"] == "accurate" and not ok["clustered"]
    assert ok["expected"] == pytest.approx(0.05 * 2750)
    # bursts of triple volatility: the count still looks right, but breaches
    # bunch up. Only the clustering test sees it, which is why we run both.
    r2 = random.Random(4)
    bursts = calm[:1500] + [r2.gauss(0, 0.01 * (3 if i % 100 < 20 else 1)) for i in range(1500)]
    b = metrics.var_backtest(bursts, dates(3000), 0.95)
    assert b["verdict"] == "accurate" and b["clustered"]
    # volatility that keeps rising: VaR built on the calmer past is always too low
    rising = [r2.gauss(0, 0.002 * 1.0012 ** i) for i in range(3000)]
    bad = metrics.var_backtest(rising, dates(3000), 0.95)
    assert bad["verdict"] == "too optimistic" and bad["rate"] > 0.05
    assert bad["exceptions"] == len(bad["exception_dates"])


def test_basel_only_for_99_and_counts_last_250_days():
    rng = random.Random(5)
    r = [rng.gauss(0, 0.01) for _ in range(800)]
    v95 = metrics.var_backtest(r, dates(800), 0.95)
    v99 = metrics.var_backtest(r, dates(800), 0.99)
    assert "basel" not in v95
    fc = metrics.rolling_var(r, 0.99, 250)
    hits = [-x > f for x, f in zip(r[250:], fc)]
    assert v99["basel"]["last_250"] == sum(hits[-250:])
    assert v99["basel"]["zone"] == metrics.basel_zone(sum(hits[-250:]))


def test_too_little_data_is_reported_not_guessed():
    assert metrics.var_backtest([0.01] * 299, dates(299)) is None


def test_runs_include_the_check_and_report_shows_it(tmp_path):
    from risklens.data import synthetic, write_csv
    write_csv(str(tmp_path / "S.csv"), synthetic(900, seed=2))
    cfg = runner.normalize({"symbol": "S", "luck_test": {"trials": 0},
                            "risk": {"vol_target": 0.15, "max_drawdown": 0.15}})
    r = runner.execute(cfg, str(tmp_path / "S.csv"))
    c = r["risk_models"]["95"]
    assert set(c["models"]) == set(metrics.RISK_MODELS) and c["best"] in c["models"]
    assert c["total_days"] == 899 - 250 and len(c["series"]["ret"]) == c["total_days"]
    html = report.render([r])
    assert "Risk model check" in html and 'class="vx"' in html and "best fit" in html
    old = dict(r, risk_models=None)                 # results saved before this feature
    assert "Not enough history" in report.render([old])


# --------------------------------------------------------------- the models
def test_normal_var_matches_formula():
    rng = random.Random(7)
    r = [rng.gauss(0.001, 0.02) for _ in range(300)]
    fc = metrics.normal_var(r, 0.99, 250)
    w = r[10:260]
    m = sum(w) / 250
    sd = (sum((x - m) ** 2 for x in w) / 249) ** 0.5
    assert fc[10] == pytest.approx(-(m - 2.3263478740408408 * sd))


def test_ewma_recursion_by_hand():
    r = [0.01, -0.02, 0.03, 0.0, -0.01]
    sig = metrics.ewma_sigma(r, lam=0.9, seed_days=2)
    v = (0.01 ** 2 + 0.02 ** 2) / 2                     # seed: mean square of the first 2 days
    assert sig[:2] == [None, None] and sig[2] == pytest.approx(v ** 0.5)
    v = 0.9 * v + 0.1 * 0.03 ** 2                       # day 2's return enters day 3's forecast
    assert sig[3] == pytest.approx(v ** 0.5)
    v = 0.9 * v + 0.1 * 0.0
    assert sig[4] == pytest.approx(v ** 0.5)


def test_fhs_equals_historical_when_volatility_is_constant():
    r = [0.01 if i % 3 else -0.01 for i in range(400)]  # |return| constant -> EWMA sigma constant
    fhs = metrics.fhs_var(r, 0.95, 250)
    hist = metrics.rolling_var(r, 0.95, 250)
    assert fhs == pytest.approx(hist)


@settings(max_examples=40, deadline=None)
@given(st.lists(st.floats(-0.2, 0.2, allow_nan=False), min_size=60, max_size=100),
       st.integers(40, 59), st.floats(-0.5, 0.5, allow_nan=False),
       st.sampled_from(sorted(metrics.VAR_MODELS)))
def test_no_model_sees_the_day_it_forecasts(rets, t, new, model):
    window = 30
    a = metrics.var_forecasts(rets, 0.95, window, model)
    changed = rets[:t] + [new] + [x * -1.7 for x in rets[t + 1:]]
    b = metrics.var_forecasts(changed, 0.95, window, model)
    assert a[:t - window + 1] == pytest.approx(b[:t - window + 1])


def test_conditional_coverage_combines_both_tests():
    rng = random.Random(3)
    hits = [rng.random() < 0.07 for _ in range(1000)]
    g = metrics.grade_var(hits, 0.95, [0.01] * 1000, [str(i) for i in range(1000)])
    assert g["cc_lr"] == pytest.approx(g["kupiec_lr"] + g["christoffersen_lr"])
    assert g["cc_p"] == pytest.approx(math.exp(-g["cc_lr"] / 2))


def test_account_history_fails_where_position_based_passes():
    """A strategy that sits in cash half the time: VaR built from its own P&L
    is far too small when it buys back in; exposure x stock VaR is not."""
    rng = random.Random(11)
    n = 3000
    asset = [rng.gauss(0.0003, 0.012) for _ in range(n)]
    exposure = [1.0 if (i // 120) % 2 else 0.0 for i in range(n)]
    pnl = [e * a for e, a in zip(exposure, asset)]
    c = metrics.compare_risk_models(pnl, asset, exposure, [str(i) for i in range(n)], 0.95)
    m = c["models"]
    assert c["test_days"] == sum(1 for e in exposure[250:] if e)
    assert not m["pnl_hist"]["passes"] and m["pnl_hist"]["verdict"] == "too optimistic"
    assert m["pos_hist"]["passes"] and m["pos_fhs"]["passes"]
    assert c["best"].startswith("pos_")
