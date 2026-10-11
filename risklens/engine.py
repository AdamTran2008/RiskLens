"""The backtest engine: one deterministic day-by-day loop.

Timeline of a trading day t
---------------------------
  1. OPEN   fill the order decided at the close of day t-1, at today's open
            price (+/- slippage) plus a fee. Orders never fill at the price
            that generated them, which removes the classic same-bar
            look-ahead bias.
  2. CLOSE  mark the position to today's close; update peak and drawdown.
  3. RISK   drawdown circuit breaker: if equity has fallen max_drawdown
            below its peak, flatten and stop trading for cooldown_days.
  4. DECIDE today's bar is appended to the History; the strategy sees bars
            0..t only and returns a target exposure; risk rules (volatility
            targeting, exposure cap, halt) adjust it; the resulting order
            waits for the next open.

Money is integer cents. Fill prices are rounded to the cent and fees to the
cent, so cash is exact and the ledger balances to the cent with no floating
point drift. Long-only, no leverage: cash can never go negative.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .data import Bar
from .strategies import History, Strategy

TRADING_DAYS = 252


def cents(x: float) -> int:
    """Round a dollar amount to integer cents, half up."""
    return math.floor(x * 100 + 0.5)


@dataclass(frozen=True)
class Costs:
    fee_bps: float = 1.0        # fee as basis points of notional
    slippage_bps: float = 2.0   # fill this far worse than the open


@dataclass(frozen=True)
class RiskConfig:
    max_exposure: float = 1.0          # max fraction of equity in the stock
    vol_target: float | None = None    # annualized volatility target, e.g. 0.15
    vol_lookback: int = 20             # days used to estimate realized vol
    max_drawdown: float | None = None  # circuit breaker, e.g. 0.15 = -15% from peak
    cooldown_days: int = 20            # days to stay flat after the breaker trips
    rebalance_band: float = 0.05       # skip trades smaller than 5% of equity


@dataclass
class Result:
    dates: list = field(default_factory=list)
    equity: list = field(default_factory=list)       # cents, at each close
    cash: list = field(default_factory=list)         # cents, at each close
    position: list = field(default_factory=list)     # shares, at each close
    close: list = field(default_factory=list)        # cents
    target_raw: list = field(default_factory=list)   # strategy's wish
    target: list = field(default_factory=list)       # after risk rules
    trades: list = field(default_factory=list)
    risk_events: list = field(default_factory=list)
    initial_cash: int = 0
    fees_paid: int = 0

    def fingerprint(self) -> str:
        """Hash of everything the run produced; equal runs => equal hash."""
        blob = json.dumps([self.dates, self.equity, self.cash, self.position,
                           self.target, self.trades, self.risk_events],
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


class InvariantError(AssertionError):
    pass


def _realized_vol(hist_bars, n):
    """Annualized close-to-close volatility of the last n returns, or None."""
    if len(hist_bars) < n + 1:
        return None
    cs = [b.close for b in hist_bars[-(n + 1):]]
    rets = [cs[i] / cs[i - 1] - 1 for i in range(1, len(cs))]
    m = sum(rets) / n
    var = sum((r - m) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def run(bars: list[Bar], strategy: Strategy, initial_cash: int = 10_000_000,
        costs: Costs = Costs(), risk: RiskConfig = RiskConfig(),
        check: bool = True, on_day=None) -> Result:
    """Run one backtest. ``initial_cash`` is in cents ($100,000 default).
    With ``check=True`` every accounting invariant is verified every day.

    ``on_day(t, bar, result, new_events)``, if given, is called at the end of
    every simulated day, after that day's books are verified. The live
    monitor uses it to stream the simulation as it happens. It only observes:
    nothing it returns can change the run."""
    if not 0 < risk.max_exposure <= 1:
        raise ValueError("max_exposure must be in (0, 1] (no leverage)")
    res = Result(initial_cash=initial_cash)
    seen: list[Bar] = []            # the ONLY bars the strategy can reach
    hist = History(seen)

    cash, pos = initial_cash, 0
    bought = sold = fees = 0        # ledger totals, cents
    pending = 0                     # share delta to execute at next open
    peak = initial_cash
    halted_until = -1               # day index when trading may resume
    slip = costs.slippage_bps / 1e4
    fee_rate = costs.fee_bps / 1e4

    def fee_of(notional_cents: int) -> int:
        return math.floor(notional_cents * fee_rate + 0.5)

    for t, bar in enumerate(bars):
        n_events = len(res.risk_events)
        # ---------------------------------------------------------- 1. OPEN
        if pending:
            if pending > 0:
                px = cents(bar.open * (1 + slip))
                # cash check at the real fill price: an overnight gap up can
                # make the order dearer than when it was decided, so shrink it
                qty = min(pending, int(cash / (px * (1 + fee_rate))))
                while qty > 0 and qty * px + fee_of(qty * px) > cash:
                    qty -= 1
                if qty < pending:
                    res.risk_events.append([bar.date, "CASH_CLIP",
                                            f"buy reduced {pending} -> {qty} shares (no leverage)"])
                if qty > 0:
                    notional = qty * px
                    fee = fee_of(notional)
                    cash -= notional + fee
                    pos += qty
                    bought += notional
                    fees += fee
                    res.trades.append([bar.date, "BUY", qty, px, fee])
            else:
                px = cents(bar.open * (1 - slip))
                qty = min(-pending, pos)
                if qty > 0:
                    notional = qty * px
                    fee = fee_of(notional)
                    cash += notional - fee
                    pos -= qty
                    sold += notional
                    fees += fee
                    res.trades.append([bar.date, "SELL", qty, px, fee])
            pending = 0

        # --------------------------------------------------------- 2. CLOSE
        close_c = cents(bar.close)
        equity = cash + pos * close_c
        if equity > peak:
            peak = equity
        drawdown = 1 - equity / peak if peak > 0 else 0.0

        # ---------------------------------------------- 3. CIRCUIT BREAKER
        halted = t < halted_until
        if risk.max_drawdown is not None:
            if t == halted_until:
                peak = equity          # cooldown over: re-arm from today's level
                drawdown = 0.0
                res.risk_events.append([bar.date, "RESUME", "cooldown over, trading resumed"])
            elif not halted and drawdown >= risk.max_drawdown:
                halted_until = t + risk.cooldown_days + 1
                halted = True
                res.risk_events.append([bar.date, "DRAWDOWN_BREAKER",
                                        f"drawdown {drawdown:.1%} >= {risk.max_drawdown:.0%}: "
                                        f"flatten, halt {risk.cooldown_days} days"])

        # ----------------------------------------------------- 4. DECIDE
        seen.append(bar)                   # today becomes visible, never tomorrow
        raw = strategy.target(hist)
        if not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise ValueError(f"{strategy.name} returned invalid target {raw!r}")
        raw = min(max(float(raw), 0.0), 1.0)
        tgt = raw
        if risk.vol_target is not None and tgt > 0:
            rv = _realized_vol(seen, risk.vol_lookback)
            if rv is not None and rv > 0:
                tgt = tgt * min(1.0, risk.vol_target / rv)
        tgt = min(tgt, risk.max_exposure)
        if halted:
            tgt = 0.0

        # size so the order is affordable after slippage and fees at today's price.
        # A price that rounds to $0.00 cannot be sized: send no order today.
        if close_c > 0:
            want = int(tgt * equity / (close_c * (1 + slip + fee_rate)))
        else:
            want = pos
            res.risk_events.append([bar.date, "UNPRICEABLE", "close rounds to $0.00; no order"])
        delta = want - pos
        # skip tiny rebalances, but always allow fully exiting or entering
        if delta and (want == 0 or pos == 0 or
                      abs(delta) * close_c >= risk.rebalance_band * equity):
            pending = delta

        res.dates.append(bar.date)
        res.equity.append(equity)
        res.cash.append(cash)
        res.position.append(pos)
        res.close.append(close_c)
        res.target_raw.append(raw)
        res.target.append(tgt)

        if check:
            if cash < 0:
                raise InvariantError(f"{bar.date}: negative cash {cash}")
            if pos < 0:
                raise InvariantError(f"{bar.date}: short position {pos}")
            if cash != initial_cash - bought + sold - fees:
                raise InvariantError(f"{bar.date}: cash ledger does not balance")
            if equity != cash + pos * close_c:
                raise InvariantError(f"{bar.date}: equity != cash + position value")
            if tgt > risk.max_exposure + 1e-12:
                raise InvariantError(f"{bar.date}: target above exposure cap")
            if halted and pending > 0:
                raise InvariantError(f"{bar.date}: buy order while halted")
        if on_day is not None:
            res.fees_paid = fees
            on_day(t, bar, res, res.risk_events[n_events:])

    res.fees_paid = fees
    return res


def to_dict(cfg) -> dict:
    return asdict(cfg)
