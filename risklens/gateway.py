"""Pre-trade risk gateway: every order is checked before it can fill.

A paper account (simulated money, one per browser session) sends orders. The
gateway runs them through eight pre-trade checks against the latest live
price and the account's limits, and fills the order only if every check
passes. It also runs the kill switch: engaged by hand, or automatically when
the account falls too far below its peak, it sells everything and blocks new
buys until a person re-arms it.

Checks, in order:
  1. Valid order       side is buy/sell, quantity > 0 with at most 8 decimals
  2. Live price        a price for the symbol arrived recently (fail closed)
  3. Kill switch       no new buys while engaged (sells always allowed)
  4. Order size        "fat finger": order value <= max_order_value
  5. Order rate        at most max_orders_per_minute submissions
  6. Cash / no short   buys: cash covers value + fee; sells: qty <= position
  7. Position limit    holding after the buy <= max_position_value
  8. Exposure limit    holdings / equity after the buy <= the lower of the
                       exposure cap and vol_target / current volatility

Invariant (tested with random order sequences): an approved order never
leaves the account breaking a limit, no buy is approved while the kill switch
is engaged or without a fresh price, and the books always reconcile:
cash = start - bought + sold - fees, exactly. Money is exact rational
arithmetic (fractions), so there is no rounding drift.

Fills are simulated at the live price plus slippage. No real orders are sent.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from fractions import Fraction as Q

SYMBOL_RE = re.compile(r"[A-Z0-9.\-=^]{1,15}")
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
MAX_SESSIONS = 500
START_CASH = 100_000


class GatewayError(ValueError):
    pass


@dataclass
class Limits:
    max_order_value: float = 25_000.0      # biggest single order, $
    max_position_value: float = 50_000.0   # biggest holding per symbol, $
    max_exposure: float = 1.0              # holdings / equity cap
    vol_target: float | None = 0.15        # exposure <= vol_target / current vol
    kill_drawdown: float | None = 0.15     # auto kill when equity falls this far below peak
    max_orders_per_minute: int = 30
    fee_bps: float = 1.0
    slippage_bps: float = 2.0


_RANGES = {"max_order_value": (1, 1e9, False), "max_position_value": (1, 1e9, False),
           "max_exposure": (0.01, 1.0, False), "vol_target": (0.01, 2.0, True),
           "kill_drawdown": (0.005, 0.99, True), "max_orders_per_minute": (1, 10_000, False),
           "fee_bps": (0, 100, False), "slippage_bps": (0, 100, False)}


def limits_from(d: dict, base: Limits | None = None) -> Limits:
    """Validate a partial limits update. Unknown fields are errors."""
    if type(d) is not dict:
        raise GatewayError("limits must be an object")
    extra = set(d) - set(_RANGES)
    if extra:
        raise GatewayError(f"unknown limit(s) {sorted(extra)}")
    out = asdict(base or Limits())
    for k, v in d.items():
        lo, hi, nullable = _RANGES[k]
        if v is None and nullable:
            out[k] = None
            continue
        if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
            raise GatewayError(f"{k} must be a number in [{lo:g}, {hi:g}]" + (" or null" if nullable else ""))
        out[k] = int(v) if k == "max_orders_per_minute" else float(v)
    return Limits(**out)


def parse_qty(v) -> Q:
    if type(v) is bool or type(v) not in (int, float, str):
        raise GatewayError("quantity must be a number")
    try:
        q = Q(str(v).strip())
    except (ValueError, ZeroDivisionError):
        raise GatewayError("quantity must be a number")
    if q <= 0 or q > 10**9:
        raise GatewayError("quantity must be above 0")
    if (q * 10**8).denominator != 1:
        raise GatewayError("quantity can have at most 8 decimals")
    return q


def _money(x) -> float:
    return round(float(x), 2)


class Account:
    def __init__(self, cash=START_CASH, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.initial = Q(cash)
        self.cash = Q(cash)
        self.pos: dict[str, Q] = {}
        self.last_px: dict[str, Q] = {}            # mark if no live price is known
        self.bought = Q(0)
        self.sold = Q(0)
        self.fees = Q(0)
        self.peak = Q(cash)
        self.kill = None
        self.log = deque(maxlen=60)
        self.recent = deque()                      # submission times, for the rate limit
        self.stats = {"checked": 0, "approved": 0, "rejected": 0}
        self.check_us = deque(maxlen=500)
        self.seq = 0

    def reconciles(self) -> bool:
        return self.cash == self.initial - self.bought + self.sold - self.fees


class Gateway:
    def __init__(self, clock=time.time, start_cash=START_CASH):
        self.clock = clock
        self.start_cash = start_cash
        self.lock = threading.Lock()
        self.quotes: dict[str, dict] = {}
        self.accounts: OrderedDict[str, Account] = OrderedDict()

    # ------------------------------------------------------------ prices
    def mark(self, symbol, price, vol=None, max_age=10.0, source="", simulated=False):
        """A new live price (called by the market stream on every update)."""
        if not (price > 0 and math.isfinite(price)):
            return
        with self.lock:
            self.quotes[symbol] = {"price": Q(str(price)), "time": self.clock(), "vol": vol,
                                   "max_age": max_age, "source": source, "simulated": simulated}
            for acct in self.accounts.values():
                if acct.pos.get(symbol):
                    acct.last_px[symbol] = Q(str(price))
                    self._after_change(acct)

    def _price(self, acct, sym):
        q = self.quotes.get(sym)
        return q["price"] if q else acct.last_px.get(sym, Q(0))

    def _equity(self, acct):
        return acct.cash + sum(qty * self._price(acct, s) for s, qty in acct.pos.items())

    def _gross(self, acct):
        return sum(qty * self._price(acct, s) for s, qty in acct.pos.items())

    def _fresh_quote(self, sym):
        q = self.quotes.get(sym)
        if q is None:
            return None, None
        age = self.clock() - q["time"]
        return q, age

    def allowed_exposure(self, acct, vol):
        lim = acct.limits
        allowed = lim.max_exposure
        why = f"cap {lim.max_exposure:.0%}"
        if lim.vol_target and vol:
            v = lim.vol_target / vol
            if v < allowed:
                allowed, why = v, f"vol target {lim.vol_target:.0%} ÷ current vol {vol:.0%}"
        return allowed, why

    # ---------------------------------------------------------- accounts
    def account(self, sid) -> Account:
        if not SESSION_RE.fullmatch(sid or ""):
            raise GatewayError("bad session id")
        a = self.accounts.get(sid)
        if a is None:
            a = self.accounts[sid] = Account(self.start_cash)
            while len(self.accounts) > MAX_SESSIONS:
                self.accounts.popitem(last=False)
        self.accounts.move_to_end(sid)
        return a

    # ----------------------------------------------------------- orders
    def submit(self, sid, order: dict) -> dict:
        with self.lock:
            acct = self.account(sid)
            t0 = time.perf_counter()
            d = self._check_and_fill(acct, order)
            us = (time.perf_counter() - t0) * 1e6
            d["check_us"] = round(us, 1)
            acct.check_us.append(us)
            return d

    def _check_and_fill(self, acct, order):
        lim = acct.limits
        now = self.clock()
        acct.seq += 1
        acct.stats["checked"] += 1
        while acct.recent and now - acct.recent[0] >= 60:
            acct.recent.popleft()
        acct.recent.append(now)
        checks = []

        def add(name, ok, detail):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            return ok

        def done(approved, sym="", side="", qty=None, px=None, notional=None):
            d = {"id": acct.seq, "time": time.strftime("%H:%M:%S", time.localtime(now)),
                 "symbol": sym, "side": side, "qty": None if qty is None else _qty_str(qty),
                 "price": None if px is None else float(px),
                 "value": None if notional is None else _money(notional),
                 "approved": approved, "checks": checks,
                 "reason": next((c["detail"] for c in checks if not c["ok"]), "All checks passed")}
            acct.stats["approved" if approved else "rejected"] += 1
            acct.log.appendleft(d)
            return d

        # 1. valid order
        if type(order) is not dict:
            add("Valid order", False, "order must be an object")
            return done(False)
        sym = str(order.get("symbol", "")).upper()
        side = order.get("side")
        try:
            if set(order) - {"symbol", "side", "qty"}:
                raise GatewayError(f"unknown field(s) {sorted(set(order) - {'symbol', 'side', 'qty'})}")
            if not SYMBOL_RE.fullmatch(sym):
                raise GatewayError("symbol must be 1-15 letters, digits or . - = ^")
            if side not in ("buy", "sell"):
                raise GatewayError("side must be buy or sell")
            qty = parse_qty(order.get("qty"))
        except GatewayError as e:
            add("Valid order", False, str(e))
            return done(False, sym, side if side in ("buy", "sell") else "")
        add("Valid order", True, f"{side} {_qty_str(qty)} {sym}")

        # 2. live price (fail closed: no price, no trade)
        q, age = self._fresh_quote(sym)
        if q is None:
            add("Live price", False, f"No live price for {sym}. Connect to it on Live market first.")
            return done(False, sym, side, qty)
        if age > q["max_age"]:
            add("Live price", False, f"Price is {age:.0f} s old (limit {q['max_age']:.0f} s). "
                                     f"Reconnect the live feed.")
            return done(False, sym, side, qty, q["price"])
        px = q["price"]
        add("Live price", True, f"${float(px):,.2f}, {age:.1f} s old"
                                + (" (simulated)" if q["simulated"] else ""))
        buy = side == "buy"
        slip = Q(str(lim.slippage_bps)) / 10_000
        fee_rate = Q(str(lim.fee_bps)) / 10_000
        fill_px = px * (1 + slip) if buy else px * (1 - slip)
        notional = qty * fill_px
        fee = notional * fee_rate
        held = acct.pos.get(sym, Q(0))

        # 3. kill switch
        if buy:
            add("Kill switch", acct.kill is None,
                "Off" if acct.kill is None else f"Engaged ({acct.kill['reason']}): no new buys")
        else:
            add("Kill switch", True, "Sells reduce risk, so they are always allowed")
        # 4. order size ("fat finger")
        add("Order size", notional <= Q(str(lim.max_order_value)),
            f"${float(notional):,.0f} vs limit ${lim.max_order_value:,.0f}")
        # 5. order rate
        add("Order rate", len(acct.recent) <= lim.max_orders_per_minute,
            f"{len(acct.recent)} orders in the last minute (limit {lim.max_orders_per_minute})")
        # 6. cash / no short selling
        if buy:
            add("Enough cash", notional + fee <= acct.cash,
                f"needs ${float(notional + fee):,.2f}, have ${float(acct.cash):,.2f}")
        else:
            add("No short selling", qty <= held,
                f"selling {_qty_str(qty)}, holding {_qty_str(held)}")
        # 7. position limit and 8. exposure limit (buys only: sells can only reduce both)
        if buy:
            new_val = (held + qty) * px
            add("Position limit", new_val <= Q(str(lim.max_position_value)),
                f"{sym} holding would be ${float(new_val):,.0f} (limit ${lim.max_position_value:,.0f})")
            equity_after = self._equity(acct) - (notional - qty * px) - fee
            gross_after = self._gross(acct) + qty * px
            allowed, why = self.allowed_exposure(acct, q["vol"])
            ok = equity_after > 0 and gross_after <= Q(allowed) * equity_after
            add("Exposure limit", ok,
                f"would be {float(gross_after / equity_after):.0%} of equity; allowed {allowed:.0%} ({why})"
                if equity_after > 0 else "equity would not stay positive")
        else:
            add("Position limit", True, "Sell: holding gets smaller")
            add("Exposure limit", True, "Sell: exposure gets smaller")

        approved = all(c["ok"] for c in checks)
        if approved:
            self._fill(acct, sym, buy, qty, notional, fee, px)
        return done(approved, sym, side, qty, fill_px, notional)

    def _fill(self, acct, sym, buy, qty, notional, fee, px):
        if buy:
            acct.cash -= notional + fee
            acct.bought += notional
            acct.pos[sym] = acct.pos.get(sym, Q(0)) + qty
        else:
            acct.cash += notional - fee
            acct.sold += notional
            acct.pos[sym] = acct.pos.get(sym, Q(0)) - qty
            if acct.pos[sym] == 0:
                del acct.pos[sym]
        acct.fees += fee
        acct.last_px[sym] = px
        assert acct.cash >= 0 and acct.reconciles()
        self._after_change(acct)

    def _after_change(self, acct):
        """Track the equity peak; trip the kill switch on a deep drawdown."""
        eq = self._equity(acct)
        if acct.kill is None:
            if eq > acct.peak:
                acct.peak = eq
            lim = acct.limits.kill_drawdown
            if lim and acct.pos and eq <= acct.peak * (1 - Q(str(lim))):
                self._engage(acct, "auto", f"account {float(1 - eq / acct.peak):.1%} below its peak "
                                           f"(limit {lim:.0%})", flatten=True)

    # ------------------------------------------------------- kill switch
    def _engage(self, acct, by, reason, flatten=True):
        acct.kill = {"by": by, "reason": reason, "time": time.strftime("%H:%M:%S",
                                                                       time.localtime(self.clock()))}
        if flatten:
            lim = acct.limits
            for sym, qty in list(acct.pos.items()):
                px = self._price(acct, sym)
                fill_px = px * (1 - Q(str(lim.slippage_bps)) / 10_000)
                notional = qty * fill_px
                fee = notional * Q(str(lim.fee_bps)) / 10_000
                acct.cash += notional - fee
                acct.sold += notional
                acct.fees += fee
                del acct.pos[sym]
                acct.seq += 1
                acct.log.appendleft({
                    "id": acct.seq, "time": acct.kill["time"], "symbol": sym, "side": "sell",
                    "qty": _qty_str(qty), "price": float(fill_px), "value": _money(notional),
                    "approved": True, "flatten": True,
                    "checks": [{"name": "Kill switch", "ok": True, "detail": "Flatten: sell everything"}],
                    "reason": "Kill switch: position closed"})
            assert acct.reconciles()

    def kill(self, sid, flatten=True):
        with self.lock:
            acct = self.account(sid)
            if acct.kill is None:
                self._engage(acct, "manual", "pressed by hand", flatten)
            elif flatten and acct.pos:
                self._engage(acct, acct.kill["by"], acct.kill["reason"], True)
            return self._state(acct)

    def rearm(self, sid):
        with self.lock:
            acct = self.account(sid)
            acct.kill = None
            acct.peak = self._equity(acct)            # measure drawdowns from here
            return self._state(acct)

    def reset(self, sid):
        with self.lock:
            limits = self.account(sid).limits
            self.accounts[sid] = Account(self.start_cash, limits)
            return self._state(self.accounts[sid])

    def set_limits(self, sid, d):
        with self.lock:
            acct = self.account(sid)
            acct.limits = limits_from(d, acct.limits)
            return self._state(acct)

    # ------------------------------------------------------------- state
    def state(self, sid):
        with self.lock:
            return self._state(self.account(sid))

    def _state(self, acct):
        eq = self._equity(acct)
        gross = self._gross(acct)
        positions = []
        for sym, qty in sorted(acct.pos.items()):
            q, age = self._fresh_quote(sym)
            px = self._price(acct, sym)
            positions.append({"symbol": sym, "qty": _qty_str(qty), "price": float(px),
                              "value": _money(qty * px),
                              "stale": q is None or age > q["max_age"]})
        lat = sorted(acct.check_us)
        quotes = {s: {"price": float(q["price"]), "age": round(self.clock() - q["time"], 1),
                      "fresh": self.clock() - q["time"] <= q["max_age"], "vol": q["vol"],
                      "allowed_exposure": self.allowed_exposure(acct, q["vol"])[0]}
                  for s, q in self.quotes.items()}
        return {
            "cash": _money(acct.cash), "equity": _money(eq), "start": _money(acct.initial),
            "pnl": _money(eq - acct.initial), "peak": _money(acct.peak),
            "drawdown": float(1 - eq / acct.peak) if acct.peak > 0 else 0.0,
            "exposure": float(gross / eq) if eq > 0 else 0.0,
            "positions": positions, "kill": acct.kill, "limits": asdict(acct.limits),
            "reconciles": acct.reconciles(), "fees": _money(acct.fees),
            "stats": dict(acct.stats, p50_us=round(lat[len(lat) // 2], 1) if lat else None,
                          p99_us=round(lat[min(len(lat) - 1, int(len(lat) * 0.99))], 1) if lat else None),
            "log": list(acct.log)[:30], "quotes": quotes,
        }


def _qty_str(q: Q) -> str:
    s = f"{float(q):.8f}".rstrip("0").rstrip(".")
    return s or "0"
