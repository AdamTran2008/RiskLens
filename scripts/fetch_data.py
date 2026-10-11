#!/usr/bin/env python3
"""Download real daily prices once and freeze them as a dataset snapshot.

    pip install yfinance
    python scripts/fetch_data.py AAPL SPY --start 2005-01-01 --end 2024-12-31

(Or just type a ticker in the web app's search box: same download, no restart.)

Writes data/<SYMBOL>.csv with split- and dividend-adjusted OHLC prices and
records each file's SHA-256 in data/manifest.json. The server refuses to
start if a file no longer matches its recorded hash, so the data behind a
result can never change silently. Data source: Yahoo Finance via the yfinance
library, used read-only and subject to Yahoo's terms. The CSV files are
git-ignored and are not redistributed with this repository.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from risklens.fetch import FetchError, add_dataset  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD, inclusive (default: latest day)")
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "data"))
    a = ap.parse_args(argv)
    failed = 0
    for sym in a.symbols:
        try:
            e = add_dataset(a.data_dir, sym, a.start, a.end)
        except FetchError as err:
            print(f"{sym.upper()}: {err}", file=sys.stderr)
            failed += 1
            continue
        print(f"{sym.upper()}: {e['rows']} bars {e['start']}..{e['end']} sha256={e['sha256'][:16]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
