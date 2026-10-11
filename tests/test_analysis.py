"""Deep-dive analytics: each answer checked against a case where the truth is
known by construction."""
import math
import random

import pytest

from risklens import analysis, engine, metrics, runner, strategies
from risklens.data import load_csv, synthetic, write_csv


def test_tails_normal_vs_fat():
    rng = random.Random(1)
    normal = [rng.gauss(0, 0.01) for _ in range(20000)]
    t = analysis.tail_risk(normal, [str(i) for i in range(20000)])
    assert abs(t["excess_kurtosis"]) < 0.15 and abs(t["skew"]) < 0.1
    b3 = t["beyond"][1]
    assert b3["sigma"] == 3 and abs(b3["observed"] - b3["expected"]) < 4 * math.sqrt(b3["expected"])
    assert sum(t["hist"]["counts"]) == 20000 and sum(t["hist"]["expected"]) == pytest.approx(20000)
    # a mixture: mostly calm days, a few wild ones -> fat tails
    fat = [rng.gauss(0, 0.01) if rng.random() < 0.95 else rng.gauss(0, 0.05) for _ in range(20000)]
    f = analysis.tail_risk(fat, [str(i) for i in range(20000)])
    assert f["excess_kurtosis"] > 5
    assert f["beyond"][2]["ratio"] > 10                        # 4-sigma days far more common
    assert f["var99_hist"] > f["var99_normal"]                  # the bell curve understates the 99% loss
    w = f["worst"]
    assert w["return"] == min(fat) and w["normal_years"] > 1


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("d")
    write_csv(str(d / "S.csv"), synthetic(1500, seed=3))
    return str(d / "S.csv")


def cfg(**kw):
    base = {"symbol": "S", "strategy": {"name": "sma_cross", "params": {"fast": 20, "slow": 80}},
            "risk": {"vol_target": 0.15, "max_drawdown": 0.15}, "luck_test": {"trials": 0}}
    return runner.normalize(base | kw)


def test_rule_attribution_endpoints_match_real_runs(data):
    c = cfg()
    bars = load_csv(data)
    a = analysis.rule_attribution(bars, c)
    keys = [v["key"] for v in a["variants"]]
    assert keys == ["none", "vol", "breaker", "all"]
    full = runner.execute(c, data)
    none = runner.execute(cfg(risk={}), data)
    allv, nonev = a["variants"][-1], a["variants"][0]
    assert allv["max_drawdown"] == pytest.approx(full["summary"]["max_drawdown"])
    assert nonev["max_drawdown"] == pytest.approx(none["summary"]["max_drawdown"])
    assert nonev["dd_cut"] == 0 and a["biggest"] in ("vol", "breaker")
    assert analysis.rule_attribution(bars, cfg(risk={})) is None


def test_robustness_grid_contains_choice_and_halves_cover_period(data):
    c = cfg()
    bars = load_csv(data)
    res = engine.run(bars, strategies.make("sma_cross", {"fast": 20, "slow": 80}),
                     round(c["initial_cash"] * 100), engine.Costs(**c["costs"]), engine.RiskConfig(**c["risk"]))
    r = analysis.robustness(bars, c, res)
    params = [g["params"] for g in r["grid"]]
    assert {"fast": 20, "slow": 80} in params and all(p["fast"] < p["slow"] for p in params)
    assert 1 <= r["chosen_rank"] <= r["n"] == len(params)
    assert r["sharpe_min"] <= r["sharpe_median"] <= r["sharpe_max"]
    h = r["halves"]
    assert h[0]["from"] == res.dates[1] and h[1]["to"] == res.dates[-1]
    bh = analysis.robustness(bars, cfg(strategy={"name": "buy_and_hold"}, risk={}), res)
    assert bh["grid"] == [] and "n" not in bh


def test_return_sources_recovers_known_beta_and_alpha():
    rng = random.Random(2)
    asset = [rng.gauss(0.0004, 0.012) for _ in range(3000)]
    strat = [0.5 * a + 0.0001 for a in asset]               # exact: beta 0.5, alpha 1 bp a day
    s = analysis.return_sources(strat, asset, [0.5] * 3000)
    assert s["beta"] == pytest.approx(0.5) and s["alpha_annual"] == pytest.approx(0.0001 * 252)
    assert s["r2"] == pytest.approx(1.0) and s["avg_exposure"] == 0.5
    noisy = [0.3 * a + rng.gauss(0, 0.01) for a in asset]
    n = analysis.return_sources(noisy, asset, [0.3] * 3000)
    assert 0.2 < n["beta"] < 0.4 and abs(n["alpha_t"]) < 3 and 0 < n["r2"] < 0.5
    flat = analysis.return_sources([0.0] * 3000, asset, [0.0] * 3000)
    assert flat["beta"] == 0 and flat["up_capture"] == 0 and flat["down_capture"] == 0


def test_regimes_split_in_thirds_and_use_only_past_volatility():
    rng = random.Random(4)
    asset = [rng.gauss(0, 0.01 * (3 if (i // 200) % 2 else 1)) for i in range(2000)]
    labels, cut = analysis.regime_labels(asset)
    assert labels[:analysis.REGIME_LOOKBACK] == [None] * analysis.REGIME_LOOKBACK
    shares = {k: labels.count(k) for k in ("calm", "normal", "turbulent")}
    assert max(shares.values()) - min(shares.values()) <= 2
    # day t's label uses returns before t only: a shock on day t can't change it
    shocked = asset[:]
    shocked[1000] = -0.5
    lab2, _ = analysis.regime_labels(shocked)
    vol_t = analysis._sd(asset[1000 - 20:1000])
    assert vol_t == analysis._sd(shocked[1000 - 20:1000])
    r = analysis.regimes(asset, asset, [1.0] * 2000)
    turb = next(g for g in r["regimes"] if g["name"] == "turbulent")
    calm = next(g for g in r["regimes"] if g["name"] == "calm")
    assert turb["asset"]["volatility"] > 2 * calm["asset"]["volatility"]


def test_runs_carry_the_deep_dive(data):
    r = runner.execute(cfg(), data)
    d = r["deep"]
    assert set(d) == {"tails", "attribution", "robustness", "sources", "regimes"}
    assert d["tails"]["days"] == 1499 and d["sources"]["beta"] < 1
    assert r["timing_ms"]["deep_dive"] > 0


@pytest.mark.parametrize("strategy,risk", [
    ({"name": "sma_cross", "params": {"fast": 20, "slow": 80}}, {"vol_target": 0.15, "max_drawdown": 0.15}),
    ({"name": "momentum", "params": {"lookback": 60}}, {}),
    ({"name": "buy_and_hold"}, {"max_exposure": 0.5}),
])
def test_report_renders_every_deep_dive_page(data, strategy, risk):
    from risklens import report
    r = runner.execute(cfg(strategy=strategy, risk=risk), data)
    html = report.render([r])
    for sid in ("summary", "varcheck", "tails", "rules", "robust", "sources", "regimes", "metrics", "luck", "method"):
        assert f'<section class="card" id="{sid}">' in html
    assert "Key findings" in html and "Deep dive" in html
    # results saved before the deep dive existed still render
    old = {k: v for k, v in r.items() if k not in ("deep", "risk_models")}
    assert 'id="tails"' in report.render([old])



@pytest.mark.parametrize("strategy,risk", [
    ({"name": "sma_cross", "params": {"fast": 20, "slow": 80}}, {"vol_target": 0.15, "max_drawdown": 0.15}),
    ({"name": "momentum", "params": {"lookback": 60}}, {}),
])
def test_cost_stress_rows_match_real_runs_and_costs_only_hurt(data, strategy, risk):
    c = cfg(strategy=strategy, risk=risk, costs={"fee_bps": 2, "slippage_bps": 3})
    bars = load_csv(data)
    res = analysis._run(bars, c, c["risk"])
    rows = analysis.robustness(bars, c, res)["costs"]
    assert [(x["mult"], x["fee_bps"], x["slippage_bps"]) for x in rows] == [(1, 2, 3), (3, 6, 9), (10, 20, 30)]
    base = analysis.stats(metrics.daily_returns(res.equity))
    assert rows[0]["cagr"] == pytest.approx(base["cagr"]) and rows[0]["fees"] == res.fees_paid / 100
    assert rows[0]["trades"] > 0
    assert rows[2]["cagr"] < rows[1]["cagr"] < rows[0]["cagr"]       # dearer trading never helps
    assert rows[2]["fees"] > rows[0]["fees"]


def test_report_shows_cost_stress_and_var_ranges(data):
    from risklens import report
    html = report.render([runner.execute(cfg(), data)])
    assert "Higher trading costs" in html and "10× higher" in html
    assert "Quantile<br>score" in html and "Wilson" in html
