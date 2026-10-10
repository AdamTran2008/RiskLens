"""Strategies. Deliberately simple: RiskLens is about trustworthy testing
and risk, not about clever signals.

A strategy is called once per day *after the close* with a History that
contains only bars up to and including today. It returns the fraction of
equity it would like to hold in the stock, in [0, 1]. The engine turns that
into an order that executes at the *next* day's open.
"""
from __future__ import annotations

from collections import deque


class History:
    """Read-only view of the bars seen so far.

    The engine appends each bar to the underlying list only when that day
    is reached, so tomorrow's bar is physically not in here yet. A strategy
    cannot peek at the future because the future has not been loaded into
    anything it can reach.
    """
    __slots__ = ("_bars",)

    def __init__(self, bars):
        self._bars = bars

    def __len__(self):
        return len(self._bars)

    @property
    def today(self):
        return self._bars[-1]

    def bar(self, ago: int = 0):
        """Bar from ``ago`` days back (0 = today). Only the past exists."""
        if ago < 0:
            raise ValueError("ago must be >= 0 (the future is not available)")
        return self._bars[-1 - ago]

    def closes(self, n: int):
        """Last n closes (oldest first), or None if fewer than n bars exist."""
        if n > len(self._bars):
            return None
        return [b.close for b in self._bars[-n:]]


class Strategy:
    name = "base"
    params: dict = {}

    def target(self, hist: History) -> float:
        raise NotImplementedError


class BuyAndHold(Strategy):
    name = "buy_and_hold"

    def __init__(self):
        self.params = {}

    def target(self, hist):
        return 1.0


class SMACross(Strategy):
    """Long when the fast moving average is above the slow one, else flat."""
    name = "sma_cross"

    def __init__(self, fast: int = 50, slow: int = 200):
        if not 1 <= fast < slow:
            raise ValueError("need 1 <= fast < slow")
        self.params = {"fast": fast, "slow": slow}
        self.fast, self.slow = fast, slow
        self._f, self._s = deque(), deque()
        self._fs = self._ss = 0.0
        self._seen = 0

    def target(self, hist):
        # incremental O(1) update using only today's bar
        c = hist.today.close
        self._seen += 1
        self._f.append(c); self._fs += c
        self._s.append(c); self._ss += c
        if len(self._f) > self.fast:
            self._fs -= self._f.popleft()
        if len(self._s) > self.slow:
            self._ss -= self._s.popleft()
        if len(self._s) < self.slow:
            return 0.0
        return 1.0 if self._fs / self.fast > self._ss / self.slow else 0.0


class Momentum(Strategy):
    """Long if the price is higher than ``lookback`` days ago, else flat."""
    name = "momentum"

    def __init__(self, lookback: int = 126):
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.params = {"lookback": lookback}
        self.lookback = lookback

    def target(self, hist):
        if len(hist) <= self.lookback:
            return 0.0
        return 1.0 if hist.today.close > hist.bar(self.lookback).close else 0.0


REGISTRY = {
    "buy_and_hold": (BuyAndHold, {}),
    "sma_cross": (SMACross, {"fast": (1, 400), "slow": (2, 1000)}),
    "momentum": (Momentum, {"lookback": (1, 1000)}),
}


def make(name: str, params: dict) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(REGISTRY)}")
    cls, schema = REGISTRY[name]
    for k, v in params.items():
        if k not in schema:
            raise ValueError(f"{name}: unknown parameter {k!r}")
        lo, hi = schema[k]
        if type(v) is not int or not lo <= v <= hi:
            raise ValueError(f"{name}: {k} must be an integer in [{lo}, {hi}]")
    return cls(**params)
