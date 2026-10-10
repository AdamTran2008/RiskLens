"""Property-based tests: Hypothesis generates thousands of random markets,
strategies, costs and risk settings and tries to break the core claims."""
import math

from hypothesis import HealthCheck, given, settings, strategies as st

from risklens.engine import Costs, RiskConfig, run
from risklens.strategies import Momentum, SMACross
from tests.helpers import Scripted, TodayClose, daily_bars, replay_ledger

SET = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# random markets: daily moves up to +-15%, overnight gaps up to +-10%
moves = st.lists(st.floats(-0.15, 0.15, allow_nan=False), min_size=5, max_size=120)


@st.composite
def market(draw):
    m = draw(moves)
    gaps = draw(st.lists(st.floats(-0.10, 0.10, allow_nan=False),
                         min_size=len(m), max_size=len(m)))
    closes, p = [], draw(st.floats(1.0, 500.0))
    for r in m:
        p = max(0.05, p * (1 + r))
        closes.append(round(p, 4))
    return daily_bars(closes, gaps)


risk_cfg = st.builds(
    RiskConfig,
    max_exposure=st.floats(0.05, 1.0),
    vol_target=st.one_of(st.none(), st.floats(0.02, 1.0)),
    vol_lookback=st.integers(2, 30),
    max_drawdown=st.one_of(st.none(), st.floats(0.02, 0.6)),
    cooldown_days=st.integers(0, 15),
    rebalance_band=st.floats(0.0, 0.2),
)
costs = st.builds(Costs, fee_bps=st.floats(0, 50), slippage_bps=st.floats(0, 50))


@SET
@given(market(), st.data(), risk_cfg, costs, st.integers(1_000, 10**9))
def test_books_always_balance(bars, data, risk, cost, cash):
    """Cash never negative, no shorts, and an independent replay of the
    trade list reproduces cash, position and equity to the cent every day.
    (run(check=True) also asserts the ledger identity internally each day.)"""
    targets = data.draw(st.lists(st.floats(0, 1), min_size=len(bars), max_size=len(bars)))
    r = run(bars, Scripted(targets), initial_cash=cash, costs=cost, risk=risk, check=True)
    assert min(r.cash) >= 0 and min(r.position) >= 0
    replay_ledger(r)
    assert r.fees_paid == sum(t[4] for t in r.trades)


@SET
@given(market(), st.data(), risk_cfg)
def test_risk_rules_always_respected(bars, data, risk):
    targets = data.draw(st.lists(st.floats(0, 1), min_size=len(bars), max_size=len(bars)))
    r = run(bars, Scripted(targets), costs=Costs(0, 0), risk=risk)
    assert max(r.target) <= risk.max_exposure + 1e-9
    # while halted by the breaker, exposure target is zero and nothing is bought
    halted = set()
    trip = None
    for d, kind, _ in r.risk_events:
        if kind == "DRAWDOWN_BREAKER":
            trip = r.dates.index(d)
        elif kind == "RESUME" and trip is not None:
            halted |= set(range(trip, r.dates.index(d)))
            trip = None
    if trip is not None:
        halted |= set(range(trip, len(r.dates)))
    for i in halted:
        assert r.target[i] == 0.0
        if i + 1 < len(r.dates):
            assert not [t for t in r.trades if t[0] == r.dates[i + 1] and t[1] == "BUY"]


@SET
@given(market(), st.integers(0, 200), st.sampled_from(["today", "sma", "mom"]))
def test_no_look_ahead(bars, cut_seed, which):
    """Change every bar AFTER day k. Everything decided up to and including
    day k must be identical: if any decision depended on the future, it
    would change."""
    k = cut_seed % len(bars)
    future = [b.__class__(b.date, b.open * 1.7, b.high * 1.7, b.low * 1.7, b.close * 1.7,
                          b.volume) for b in bars[k + 1:]]
    other = bars[:k + 1] + future

    def make():
        return {"today": TodayClose(), "sma": SMACross(2, 5), "mom": Momentum(3)}[which]

    a = run(bars, make(), costs=Costs(1, 2), risk=RiskConfig(vol_target=0.2, max_drawdown=0.3))
    b = run(other, make(), costs=Costs(1, 2), risk=RiskConfig(vol_target=0.2, max_drawdown=0.3))
    assert a.target[:k + 1] == b.target[:k + 1]
    assert a.equity[:k + 1] == b.equity[:k + 1]
    assert [t for t in a.trades if t[0] <= bars[k].date] == \
           [t for t in b.trades if t[0] <= bars[k].date]


@SET
@given(market(), risk_cfg, costs)
def test_deterministic(bars, risk, cost):
    r1 = run(bars, SMACross(2, 4), costs=cost, risk=risk)
    r2 = run(bars, SMACross(2, 4), costs=cost, risk=risk)
    assert r1.fingerprint() == r2.fingerprint()


@SET
@given(market())
def test_flat_strategy_changes_nothing(bars):
    r = run(bars, Scripted([0.0] * len(bars)))
    assert r.trades == [] and set(r.equity) == {r.initial_cash}
