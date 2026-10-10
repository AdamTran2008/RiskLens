#!/usr/bin/env python3
"""Download real daily prices once and freeze them as a dataset snapshot.

    pip install yfinance
    python scripts/fetch_data.py AAPL SPY --start 2010-01-01 --end 2024-12-31

Writes data/<SYMBOL>.csv with split- and dividend-adjusted OHLC prices
(auto_adjust=True) and records each file's SHA-256 in data/manifest.json.
The server refuses to start if a file no longer matches its recorded hash,
so the data behind a result can never change silently.

Data acquisition is deliberately kept outside the simulation: backtests run
from the frozen snapshot, never from a live API, so they are reproducible
and the demo works offline. Data source: Yahoo Finance via the yfinance
library, used read-only and subject to Yahoo's terms. The CSV files are
git-ignored and are not redistributed with this repository.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from risklens.data import Bar, register, write_csv  # noqa: E402


def fetch(symbol: str, start: str, end: str) -> list[Bar]:
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("yfinance is not installed: pip install yfinance")
    # yfinance's `end` is exclusive, so ask for one extra day
    end_excl = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    df = yf.Ticker(symbol).history(start=start, end=end_excl, interval="1d",
                                   auto_adjust=True, actions=False)
    if df is None or df.empty:
        raise SystemExit(f"{symbol}: no data returned (check the symbol and dates)")
    bars = []
    for ts, row in df.iterrows():
        o, h, l, c, v = (float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume"))
        if any(x != x for x in (o, h, l, c)):      # skip NaN rows (e.g. holidays)
            continue
        # adjusted OHLC can disagree with high/low by rounding; keep them consistent
        h = max(h, o, c)
        l = min(l, o, c)
        bars.append(Bar(ts.strftime("%Y-%m-%d"), o, h, l, c, v))
    return bars


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args(argv)
    os.makedirs(a.data_dir, exist_ok=True)
    for sym in a.symbols:
        sym = sym.upper()
        bars = fetch(sym, a.start, a.end)
        fn = f"{sym}.csv"
        write_csv(os.path.join(a.data_dir, fn), bars)
        e = register(a.data_dir, sym, fn,
                     source=f"Yahoo Finance via yfinance, adjusted, fetched {dt.date.today()}")
        print(f"{sym}: {e['rows']} bars {e['start']}..{e['end']} sha256={e['sha256'][:16]}")


if __name__ == "__main__":
    main()
