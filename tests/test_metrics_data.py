"""Metrics checked against hand calculations; data loading must reject bad
files instead of silently fixing them."""
import math
import sys
import types

import pytest

from risklens import metrics
from risklens.data import Bar, DataError, load_csv, register, sha256_file, synthetic, write_csv


def test_max_drawdown_hand():
    eq = [100, 120, 90, 130, 65, 140]
    dd = metrics.max_drawdown(eq, [f"d{i}" for i in range(6)])
    assert dd["max_drawdown"] == pytest.approx(0.5)            # 130 -> 65
    assert (dd["peak_date"], dd["trough_date"]) == ("d3", "d4")
    assert dd["recovery_days"] == 1                              # back above 130 at d5


def test_max_drawdown_never_recovered():
    assert metrics.max_drawdown([100, 80, 90])["recovery_days"] is None


def test_var_cvar_nearest_rank():
    rets = [-0.05, -0.04, -0.03] + [0.01] * 37                  # n = 40
    var, cvar = metrics.var_cvar(rets, 0.95)                     # k = ceil(0.05*40) = 2
    assert var == pytest.approx(0.04)                            # 2nd worst day
    assert cvar == pytest.approx(0.045)                          # mean of 2 worst


def test_sharpe_and_vol_hand():
    eq = [100.0]
    for r in [0.01, -0.01] * 50:
        eq.append(eq[-1] * (1 + r))
    s = metrics.summary([round(e * 1e6) for e in eq], [str(i) for i in range(len(eq))],
                        [1] * len(eq), [], 0)
    rets = metrics.daily_returns([round(e * 1e6) for e in eq])
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1))
    assert s["volatility"] == pytest.approx(sd * math.sqrt(252))
    assert s["sharpe"] == pytest.approx(mean / sd * math.sqrt(252))
    assert abs(s["sharpe"]) < 0.05                                # zig-zag: no edge


def test_luck_test_detects_foresight_and_constant():
    import random
    rng = random.Random(1)
    a = [rng.gauss(0, 0.01) for _ in range(500)]
    perfect = [1.0 if x > 0 else 0.0 for x in a]                  # cheats: knows today
    lt = metrics.luck_test(a, perfect, trials=300, seed=3)
    assert lt["p_value"] < 0.01
    const = metrics.luck_test(a, [1.0] * 500, trials=100, seed=3)
    assert const["p_value"] == 1.0                                # shuffling changes nothing
    again = metrics.luck_test(a, perfect, trials=300, seed=3)
    assert again == lt                                            # seeded: reproducible


# ------------------------------------------------------------------ data
def _write(tmp_path, rows, header="Date,Open,High,Low,Close,Volume"):
    p = tmp_path / "x.csv"
    p.write_text(header + "\n" + "\n".join(rows) + "\n")
    return str(p)


GOOD = ["2020-01-02,10,11,9,10.5,100", "2020-01-03,10.5,12,10,11,100"]


def test_load_good(tmp_path):
    assert len(load_csv(_write(tmp_path, GOOD))) == 2


@pytest.mark.parametrize("rows,header", [
    (GOOD[::-1], None),                                           # unsorted
    ([GOOD[0], GOOD[0]], None),                                   # duplicate date
    (["2020-01-02,10,11,9,-1,100", GOOD[1]], None),               # negative price
    (["2020-01-02,10,10.2,9,10.5,100", GOOD[1]], None),           # high < close
    (["2020-01-02,10,11,9,nan,100", GOOD[1]], None),              # NaN
    (["2020/01/02,10,11,9,10.5,100", GOOD[1]], None),             # bad date
    (["2020-01-02,10,11,9,100", GOOD[1]], None),                  # missing column
    (GOOD, "date,open,high,low,close,volume"),                    # wrong header
])
def test_load_rejects_bad_data(tmp_path, rows, header):
    with pytest.raises(DataError):
        load_csv(_write(tmp_path, rows, header or "Date,Open,High,Low,Close,Volume"))


def test_synthetic_is_deterministic_and_valid(tmp_path):
    assert synthetic(300, seed=5) == synthetic(300, seed=5)
    assert synthetic(300, seed=5) != synthetic(300, seed=6)
    write_csv(str(tmp_path / "S.csv"), synthetic(300, seed=5))
    e = register(str(tmp_path), "S", "S.csv", source="test")
    assert e["rows"] == 300 and e["sha256"] == sha256_file(str(tmp_path / "S.csv"))


def test_fetch_script_with_fake_yfinance(tmp_path, monkeypatch):
    """The fetch script is exercised offline with a stand-in for yfinance."""
    pd = pytest.importorskip("pandas")
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]).tz_localize("America/New_York")
    df = pd.DataFrame({"Open": [10.0, 10.5, float("nan")], "High": [10.6, 10.9, 1.0],
                       "Low": [9.8, 10.1, 1.0], "Close": [10.4, 10.7, 1.0],
                       "Volume": [100, 200, 0]}, index=idx)
    calls = {}

    class FakeTicker:
        def __init__(self, sym):
            calls["sym"] = sym

        def history(self, **kw):
            calls.update(kw)
            return df
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
    sys.path.insert(0, "scripts")
    import fetch_data
    fetch_data.main(["aapl", "--start", "2024-01-01", "--end", "2024-01-04",
                     "--data-dir", str(tmp_path)])
    assert calls["sym"] == "AAPL" and calls["auto_adjust"] is True
    assert calls["end"] == "2024-01-05"                           # end made inclusive
    bars = load_csv(str(tmp_path / "AAPL.csv"))
    assert [b.date for b in bars] == ["2024-01-02", "2024-01-03"]  # NaN row dropped
