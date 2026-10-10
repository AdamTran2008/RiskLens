"""Price data: strict CSV loading, dataset fingerprints, synthetic data.

A dataset is a CSV with header ``Date,Open,High,Low,Close,Volume`` holding
daily bars in ascending date order. Prices are expected to be split- and
dividend-adjusted (``scripts/fetch_data.py`` asks yfinance for adjusted
prices), so the engine never has to handle corporate actions itself.

Every dataset is identified by the SHA-256 of its file bytes. That hash goes
into every run's manifest, so a result can always be traced back to the
exact data it was computed from.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
from dataclasses import dataclass

HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


class DataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Bar:
    date: str      # ISO yyyy-mm-dd
    open: float
    high: float
    low: float
    close: float
    volume: float


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(s: str, what: str, line: int) -> float:
    try:
        v = float(s)
    except ValueError:
        raise DataError(f"line {line}: {what} is not a number: {s!r}")
    if not math.isfinite(v):
        raise DataError(f"line {line}: {what} is not finite")
    return v


def load_csv(path: str) -> list[Bar]:
    """Load and validate a dataset. Rejects (never silently fixes) bad data:
    unsorted or duplicate dates, non-positive prices, high/low inconsistent
    with open/close, missing columns."""
    bars: list[Bar] = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if header != HEADER:
            raise DataError(f"expected header {HEADER}, got {header}")
        prev = None
        for i, row in enumerate(r, start=2):
            if not row:
                continue
            if len(row) != 6:
                raise DataError(f"line {i}: expected 6 columns, got {len(row)}")
            d = row[0]
            if len(d) != 10 or d[4] != "-" or d[7] != "-":
                raise DataError(f"line {i}: bad date {d!r}")
            if prev is not None and d <= prev:
                raise DataError(f"line {i}: dates must be strictly increasing ({prev} -> {d})")
            o, h, l, c = (_num(row[k], HEADER[k], i) for k in range(1, 5))
            v = _num(row[5], "Volume", i)
            if min(o, h, l, c) <= 0:
                raise DataError(f"line {i}: prices must be positive")
            if h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9:
                raise DataError(f"line {i}: high/low inconsistent with open/close")
            bars.append(Bar(d, o, h, l, c, v))
            prev = d
    if len(bars) < 2:
        raise DataError("dataset needs at least 2 bars")
    return bars


def write_csv(path: str, bars: list[Bar]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for b in bars:
            w.writerow([b.date, f"{b.open:.6f}", f"{b.high:.6f}", f"{b.low:.6f}",
                        f"{b.close:.6f}", f"{b.volume:.0f}"])


def slice_dates(bars: list[Bar], start: str | None, end: str | None) -> list[Bar]:
    return [b for b in bars if (start is None or b.date >= start)
            and (end is None or b.date <= end)]


# ------------------------------------------------------------- manifest
def load_manifest(data_dir: str) -> dict:
    p = os.path.join(data_dir, "manifest.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def save_manifest(data_dir: str, manifest: dict) -> None:
    p = os.path.join(data_dir, "manifest.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    os.replace(tmp, p)


def register(data_dir: str, symbol: str, filename: str, source: str, note: str = "") -> dict:
    """Validate a dataset file and record its fingerprint in manifest.json."""
    path = os.path.join(data_dir, filename)
    bars = load_csv(path)
    entry = {"file": filename, "sha256": sha256_file(path), "rows": len(bars),
             "start": bars[0].date, "end": bars[-1].date, "source": source}
    if note:
        entry["note"] = note
    m = load_manifest(data_dir)
    m[symbol] = entry
    save_manifest(data_dir, m)
    return entry


# ------------------------------------------------------------ synthetic
def synthetic(n_days: int = 2520, seed: int = 7, start_price: float = 100.0,
              start_date: str = "2015-01-02") -> list[Bar]:
    """Seeded synthetic daily prices with regimes: calm bull, a crash, a
    volatile recovery and a choppy sideways market. Used for tests,
    benchmarks and offline demos. Not real market data."""
    import datetime as dt
    rng = random.Random(seed)
    # (fraction of days, daily drift, daily vol)
    regimes = [(0.35, 0.0006, 0.010), (0.06, -0.0060, 0.035),
               (0.24, 0.0012, 0.022), (0.35, 0.0000, 0.015)]
    # one 10-year cycle of regimes, repeated for longer histories
    cycle_len = min(n_days, 2520)
    cycle = []
    for frac, mu, sig in regimes:
        cycle += [(mu, sig)] * int(frac * cycle_len)
    cycle += [regimes[-1][1:]] * (cycle_len - len(cycle))
    params = [cycle[i % cycle_len] for i in range(n_days)]
    d = dt.date.fromisoformat(start_date)
    bars = []
    prev_close = start_price
    for mu, sig in params:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        gap = rng.gauss(0, sig * 0.3)
        o = prev_close * math.exp(gap)
        c = o * math.exp(mu - 0.5 * sig * sig + rng.gauss(0, sig))
        hi = max(o, c) * math.exp(abs(rng.gauss(0, sig * 0.5)))
        lo = min(o, c) * math.exp(-abs(rng.gauss(0, sig * 0.5)))
        bars.append(Bar(d.isoformat(), round(o, 6), round(hi, 6), round(lo, 6),
                        round(c, 6), float(rng.randint(1_000_000, 5_000_000))))
        prev_close = round(c, 6)
        d += dt.timedelta(days=1)
    return bars


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Create the synthetic SYNTH dataset")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--days", type=int, default=2520)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    os.makedirs(a.data_dir, exist_ok=True)
    write_csv(os.path.join(a.data_dir, "SYNTH.csv"), synthetic(a.days, a.seed))
    e = register(a.data_dir, "SYNTH", "SYNTH.csv", source=f"synthetic seed={a.seed}",
                 note="generated data, not real prices")
    print(f"SYNTH: {e['rows']} bars {e['start']}..{e['end']} sha256={e['sha256'][:16]}")


if __name__ == "__main__":
    main()
