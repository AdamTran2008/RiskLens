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
    v = r["var_check"]["95"]
    assert v["days"] == 899 - 250 and len(v["series"]["var"]) == v["days"]
    html = report.render([r])
    assert "VaR check" in html and 'class="vx"' in html
    old = dict(r, var_check=None)                   # results saved before this feature
    assert "Not enough history" in report.render([old])
