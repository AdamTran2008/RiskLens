"""Shared test helpers: tiny bar builders, scripted strategies, an
independent ledger replay used to cross-check the engine's accounting."""
from risklens.data import Bar
from risklens.strategies import Strategy


def bars_from(prices, start_day=1):
    """prices: list of (open, close). High/low are set around them."""
    import datetime as dt
    d0 = dt.date(2020, 1, start_day)
    return [Bar((d0 + dt.timedelta(days=i)).isoformat(), o, max(o, c) * 1.01,
                min(o, c) * 0.99, c, 1000.0) for i, (o, c) in enumerate(prices)]


def daily_bars(closes, gaps=None):
    """Bars with unique increasing dates for any length. Open = previous
    close times (1 + gap)."""
    import datetime as dt
    d0 = dt.date(2000, 1, 3)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        g = gaps[i] if gaps else 0.0
        o = prev * (1 + g)
        out.append(Bar((d0 + dt.timedelta(days=i)).isoformat(), o, max(o, c) * 1.001,
                       min(o, c) * 0.999, c, 1000.0))
        prev = c
    return out


class Scripted(Strategy):
    """Returns targets[i] on day i (i = number of bars seen - 1)."""
    name = "scripted"

    def __init__(self, targets):
        self.targets = list(targets)
        self.params = {}

    def target(self, hist):
        i = len(hist) - 1
        return self.targets[i] if i < len(self.targets) else 0.0


class TodayClose(Strategy):
    """Uses only today's close: long when the close ends in an even cent."""
    name = "today_close"
    params = {}

    def target(self, hist):
        return 1.0 if int(round(hist.today.close * 100)) % 2 == 0 else 0.0


def replay_ledger(res):
    """Rebuild cash and position from the trade list alone and compare with
    the engine's daily records. Independent of engine internals."""
    cash, pos = res.initial_cash, 0
    by_date = {}
    for tr in res.trades:
        by_date.setdefault(tr[0], []).append(tr)
    for i, d in enumerate(res.dates):
        for _, side, qty, px, fee in by_date.get(d, []):
            if side == "BUY":
                cash -= qty * px + fee
                pos += qty
            else:
                cash += qty * px - fee
                pos -= qty
        assert cash == res.cash[i], (d, cash, res.cash[i])
        assert pos == res.position[i], (d, pos, res.position[i])
        assert res.equity[i] == cash + pos * res.close[i]
    return cash, pos
