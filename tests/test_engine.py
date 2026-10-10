"""Hand-calculated scenarios: every expected number below was worked out on
paper from the engine's documented rules."""
import pytest

from risklens.engine import Costs, RiskConfig, run
from risklens.strategies import BuyAndHold, History, Momentum, SMACross
from tests.helpers import Scripted, bars_from, replay_ledger

FREE = Costs(fee_bps=0, slippage_bps=0)


def test_buy_and_hold_hand_calculated():
    # $1,000 cash. Day 1 close 10 -> want 100 shares, filled at day 2 OPEN (11),
    # but 100 x $11 = $1,100 > $1,000, so the fill is clipped to 90 shares.
    bars = bars_from([(10, 10), (11, 12), (12, 9), (9, 10)])
    r = run(bars, BuyAndHold(), initial_cash=100_000, costs=FREE)
    assert r.trades == [["2020-01-02", "BUY", 90, 1100, 0]]
    assert r.cash == [100_000, 1_000, 1_000, 1_000]
    assert r.position == [0, 90, 90, 90]
    assert r.equity == [100_000, 109_000, 82_000, 91_000]   # $910.00 at the end
    assert any(e[1] == "CASH_CLIP" for e in r.risk_events)
    replay_ledger(r)


def test_order_decided_at_close_fills_at_next_open():
    bars = bars_from([(10, 10), (10, 20), (30, 30), (40, 40)])
    r = run(bars, Scripted([1, 0, 0, 0]), initial_cash=100_000, costs=FREE)
    # decided day 1 close -> bought day 2 OPEN @10 (not the day-2 close of 20);
    # exit decided day 2 close -> sold day 3 OPEN @30
    assert r.trades == [["2020-01-02", "BUY", 100, 1000, 0], ["2020-01-03", "SELL", 100, 3000, 0]]
    assert r.equity[-1] == 100_000 - 100_000 + 300_000


def test_fees_and_slippage_rounded_to_cents():
    bars = bars_from([(10, 10), (10, 10), (10, 10)])
    r = run(bars, Scripted([0.5, 0, 0]), initial_cash=100_000,
            costs=Costs(fee_bps=10, slippage_bps=100))
    # want = int(0.5 * 100000 / (1000 * 1.011)) = 49 shares; buy px = 10 * 1.01 = $10.10
    # fee = round(49 * 1010 * 0.001) = round(49.49) = 49 cents
    assert r.trades[0] == ["2020-01-02", "BUY", 49, 1010, 49]
    # sell px = 10 * 0.99 = $9.90; fee = round(49 * 990 * 0.001) = round(48.51) = 49
    assert r.trades[1] == ["2020-01-03", "SELL", 49, 990, 49]
    assert r.cash[-1] == 100_000 - 49 * 1010 - 49 + 49 * 990 - 49
    assert r.fees_paid == 98
    replay_ledger(r)


def test_last_day_decision_never_fills():
    bars = bars_from([(10, 10), (10, 10)])
    r = run(bars, Scripted([0, 1]), initial_cash=100_000, costs=FREE)
    assert r.trades == [] and r.position[-1] == 0


def test_rebalance_band_skips_tiny_trades():
    bars = bars_from([(10, 10)] * 6)
    r = run(bars, Scripted([1, 0.98, 0.97, 0.99, 0, 0]), initial_cash=100_000, costs=FREE,
            risk=RiskConfig(rebalance_band=0.05))
    sides = [t[1] for t in r.trades]
    assert sides == ["BUY", "SELL"]          # the 1-3% wiggles never traded


def test_exposure_cap():
    bars = bars_from([(10, 10)] * 4)
    r = run(bars, BuyAndHold(), initial_cash=100_000, costs=FREE,
            risk=RiskConfig(max_exposure=0.5))
    assert r.target[0] == 0.5
    assert r.position[1] == 50                # $500 of $1,000


def test_no_leverage_allowed():
    with pytest.raises(ValueError):
        run(bars_from([(10, 10)] * 3), BuyAndHold(), risk=RiskConfig(max_exposure=1.5))


def test_drawdown_breaker_flattens_and_halts():
    # buy at 10, rally to 20 (equity doubles, new peak), then fall 5% a day.
    # The breaker must measure from the NEW peak, not from the starting cash.
    closes = [10, 10, 15, 20] + [20 * 0.95 ** k for k in range(1, 10)] + [16] * 30
    bars = bars_from([(c, c) for c in closes])
    r = run(bars, BuyAndHold(), initial_cash=100_000, costs=FREE,
            risk=RiskConfig(max_drawdown=0.20, cooldown_days=5))
    trips = [e for e in r.risk_events if e[1] == "DRAWDOWN_BREAKER"]
    resumes = [e for e in r.risk_events if e[1] == "RESUME"]
    assert trips and resumes
    t_trip = r.dates.index(trips[0][0])
    t_resume = r.dates.index(resumes[0][0])
    assert t_resume == t_trip + 6            # 5 cooldown days, resumes on the 6th
    assert r.position[t_trip + 1] == 0       # flat at the next open
    for i in range(t_trip, t_resume):
        assert r.target[i] == 0.0            # no exposure while halted
    halt_dates = set(r.dates[t_trip + 1: t_resume + 1])
    assert not [t for t in r.trades if t[1] == "BUY" and t[0] in halt_dates]
    assert r.position[-1] > 0                # trading resumed afterwards
    assert r.equity[t_trip] > 100_000        # tripped while still above starting cash


def test_vol_target_scales_down_in_volatile_markets():
    calm = [100 * (1.001 ** i) for i in range(40)]
    wild = [calm[-1] * (1.05 if i % 2 else 0.95) for i in range(40)]
    bars = bars_from([(c, c) for c in calm + wild])
    r = run(bars, BuyAndHold(), initial_cash=1_000_000, costs=FREE,
            risk=RiskConfig(vol_target=0.15, vol_lookback=20, rebalance_band=0.0))
    assert r.target[35] == 1.0               # calm: tiny vol, full size
    assert r.target[-1] < 0.2                # ~10% daily moves (~160%/yr) -> ~0.1


def test_history_has_no_future():
    seen_lengths = []

    class Peeker(Scripted):
        def target(self, hist):
            seen_lengths.append(len(hist))
            with pytest.raises(ValueError):
                hist.bar(-1)                  # asking for tomorrow is refused
            with pytest.raises(IndexError):
                hist._bars[len(hist)]         # and tomorrow is physically absent
            return 0.0

    bars = bars_from([(10, 10)] * 5)
    run(bars, Peeker([]), costs=FREE)
    assert seen_lengths == [1, 2, 3, 4, 5]


def test_strategies_basic():
    h = History(bars_from([(c, c) for c in range(1, 8)]))
    assert h.closes(3) == [5, 6, 7] and h.closes(99) is None
    m = Momentum(lookback=3)
    assert m.target(h) == 1.0
    s = SMACross(fast=2, slow=3)
    seen = []
    hh = History(seen)
    outs = []
    for b in bars_from([(c, c) for c in [5, 4, 3, 4, 6, 8]]):
        seen.append(b)
        outs.append(s.target(hh))
    # day 4: fast (3+4)/2=3.5 < slow (4+3+4)/3=3.67 -> flat; day 5: 5 > 4.33 -> long
    assert outs == [0.0, 0.0, 0.0, 0.0, 1.0, 1.0]


def test_invalid_strategy_output_rejected():
    with pytest.raises(ValueError):
        run(bars_from([(10, 10)] * 3), Scripted([float("nan")] * 3), costs=FREE)


def test_price_rounding_to_zero_cents_is_safe():
    # $0.004 rounds to $0.00: the engine must not divide by zero or buy
    bars = bars_from([(1, 1), (0.5, 0.004), (0.004, 0.004), (0.01, 0.01)])
    r = run(bars, BuyAndHold(), initial_cash=100_000, costs=FREE)
    replay_ledger(r)
    assert r.target[1] == 1.0 and r.position[2] == r.position[1]   # no new order sized at $0
