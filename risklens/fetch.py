"""Download real daily prices and freeze them as a dataset snapshot.

Data acquisition is deliberately kept outside the engine: backtests always
run from the frozen, hashed CSV, never from a live API, so results are
reproducible and work offline. Used by scripts/fetch_data.py and by the web
app's "Add stock" button (POST /api/datasets).

Source: Yahoo Finance via the yfinance library, split- and dividend-adjusted
(auto_adjust=True), read-only and subject to Yahoo's terms. Downloaded files
are git-ignored and not redistributed.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from .data import Bar, register, write_csv

SYMBOL_RE = re.compile(r"[A-Z0-9.\-=^]{1,15}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class FetchError(Exception):
    pass


def check_symbol(symbol) -> str:
    sym = symbol.strip().upper() if isinstance(symbol, str) else ""
    if not SYMBOL_RE.fullmatch(sym):
        raise FetchError("symbol must be 1-15 letters, digits or . - = ^ (for example AAPL, BRK-B, ^GSPC)")
    return sym


def check_date(d, name) -> str | None:
    if d in (None, ""):
        return None
    if not isinstance(d, str) or not DATE_RE.fullmatch(d):
        raise FetchError(f"{name} must be YYYY-MM-DD")
    try:
        dt.date.fromisoformat(d)
    except ValueError:
        raise FetchError(f"{name} is not a real date")
    return d


DEFAULT_START = "2005-01-01"      # covers the 2008 crash, COVID and 2022 for stress tests


def yahoo_daily(symbol: str, start: str = DEFAULT_START, end: str | None = None) -> list[Bar]:
    """Daily adjusted bars from Yahoo Finance, oldest first. ``end`` is
    inclusive; None means up to the latest available day."""
    try:
        import yfinance as yf
    except ImportError:
        raise FetchError("downloading needs one package: pip install yfinance")
    kw = {"start": start, "interval": "1d", "auto_adjust": True, "actions": False}
    if end:                                   # yfinance's end is exclusive
        kw["end"] = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    try:
        df = yf.Ticker(symbol).history(**kw)
    except Exception as e:                    # network, rate limit, bad symbol
        raise FetchError(f"Yahoo Finance request failed: {e}")
    if df is None or df.empty:
        raise FetchError(f"no prices found for {symbol} (check the symbol on finance.yahoo.com)")
    bars = []
    for ts, row in df.iterrows():
        o, h, l, c, v = (float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume"))
        if any(x != x or x <= 0 for x in (o, h, l, c)):   # skip NaN or empty rows
            continue
        # adjusted OHLC can disagree with high/low by rounding; keep them consistent
        bars.append(Bar(ts.strftime("%Y-%m-%d"), o, max(h, o, c), min(l, o, c), c, v if v == v else 0.0))
    if not bars:
        raise FetchError(f"no valid prices for {symbol}")
    return bars


def add_dataset(data_dir: str, symbol: str, start: str = DEFAULT_START, end: str | None = None,
                fetcher=yahoo_daily) -> dict:
    """Download, freeze and register one dataset; returns its manifest entry.
    The CSV is written to a temporary file and moved into place, so a failed
    download never leaves a half-written dataset."""
    sym = check_symbol(symbol)
    start = check_date(start, "start") or DEFAULT_START
    end = check_date(end, "end")
    if end and end < start:
        raise FetchError("start is after end")
    bars = fetcher(sym, start, end)
    os.makedirs(data_dir, exist_ok=True)
    fn = f"{sym}.csv"
    path = os.path.join(data_dir, fn)
    tmp = path + ".part"
    write_csv(tmp, bars)
    os.replace(tmp, path)
    return register(data_dir, sym, fn, source=f"Yahoo Finance via yfinance, adjusted, "
                                              f"fetched {dt.date.today()}")
