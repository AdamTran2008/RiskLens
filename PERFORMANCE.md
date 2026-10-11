# RiskLens performance report

Follows Lucera's template: environment, workload, metric, procedure,
interpretation. Every number comes from `python3 bench/bench.py`; raw JSON
is in `bench/results/`.

## 1. Environment
| | |
|---|---|
| CPU | Intel Xeon @ 2.80 GHz, **2 vCPU** (cloud VM) |
| Memory | 8 GB VM |
| OS / runtime | Linux 6.18, CPython 3.13.16, standard library only |
| Confinement | engine benchmarks pinned to **CPU 0** (`sched_setaffinity`); server benchmark: server process pinned to **CPU 0** (`taskset -c 0`), HTTP clients pinned to **CPU 1** |
| Transport | HTTP over loopback (127.0.0.1) |

## 2. Workloads
All data is **synthetic and seeded** (`risklens.data.synthetic`), so anyone
can regenerate identical inputs: a repeating 10-year cycle of regimes (calm
bull market, crash, volatile recovery, sideways chop).

* **Engine:** SMA(50/200) trend strategy **with all risk rules on** (15% vol
  target, 15% drawdown breaker, exposure cap). 10, 100 and 1,000 years of
  daily bars, with and without per-day invariant checking. Best of 3.
* **Full run:** 10 years (2,520 bars) through the complete pipeline: load +
  validate CSV, simulate, metrics, 1,000-shuffle luck test. Best of 5.
* **Luck test:** 2,519 days, 100 / 1,000 / 5,000 shuffles.
* **Server:** 10-year dataset. *Cold:* 20 distinct configs, each computed.
  *Hot:* one config requested 500 times by 1 client, then 2,000 times by 8
  concurrent clients (served from the result store).

## 3. Results

### Engine throughput
| Data | Bars | Invariants checked | Time | Bars/s |
|---|---:|---|---:|---:|
| 10 years | 2,520 | yes | 13.9 ms | **181,078** |
| 10 years | 2,520 | no | 13.0 ms | 193,282 |
| 100 years | 25,200 | yes | 144 ms | 174,701 |
| 1,000 years | 252,000 | yes | 1.42 s | 177,357 |
| 1,000 years | 252,000 | no | 1.39 s | 181,326 |

* Peak traced memory for 100 years: 4.3 MB (~169 bytes per bar).
* Same inputs twice gave the same result fingerprint (`deterministic_fingerprint_match: true`).

### Where a full 10-year run spends its time
| Stage | ms | Share |
|---|---:|---:|
| Load + validate CSV | 9.9 | 1% |
| Simulate (engine) | 11.9 | 1% |
| Luck test (1,000 shuffles) | 851.9 | **97%** |
| **Total** | 875.3 | |

### Luck-test scaling
| Shuffles | Seconds |
|---:|---:|
| 100 | 0.10 |
| 1,000 | 0.89 |
| 5,000 | 4.29 |

### Server (server on CPU 0, clients on CPU 1)
| Request | p50 | p90 | p99 | max | Throughput |
|---|---:|---:|---:|---:|---:|
| New run, computed (n=20) | 914 ms | 1,090 ms | 1,148 ms | 1,148 ms | |
| Cached result, 1 client (n=500) | **1.98 ms** | 2.34 ms | 3.63 ms | 5.0 ms | |
| Cached result, 8 clients (n=2,000) | 16.2 ms | 22.8 ms | 33.0 ms | 39.8 ms | **455 req/s** |

Server peak RSS: 28 MB. Server stats confirmed 20 runs computed and 2,500 cache hits.

### Live market pipeline (`bench/results/live.json`)
Run on a 2-vCPU Intel Xeon @ 2.10 GHz VM (a different VM from the tables
above), CPython 3.13. Workload: 200 BTC-USD trades, one every 120 ms, fed
into the real `CoinbaseProvider` through an in-memory fake socket, over a
session of 300 backfilled 1-minute bars; an SSE client in the same process
reads the updates over loopback.

| Stage | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|
| Trade message arrives → risk update read by the client | **0.88 ms** | 1.08 ms | 2.08 ms | 14.7 ms |
| Risk snapshot inside that (300 bars) | 0.13 ms | 0.18 ms | 0.26 ms | 0.27 ms |
| Risk snapshot alone, 1,440 bars (a full day) | 0.18 ms | 0.30 ms | 0.49 ms | 3.93 ms |

**Not included, and measured live in the app instead:** the internet hop
from Coinbase to your machine ("Exchange → server", Coinbase's trade
timestamp vs. your clock) and browser rendering. The app's latency panel
shows medians of the last 40 updates for exchange → server, risk
calculation, server → browser, and trade → your screen.

### Pre-trade risk gateway (`bench/results/gateway.json`)
Same 2.1 GHz VM. Workload: 10,000 orders (alternating buy/sell 0.01 BTC)
through all eight checks and the simulated fill on one paper account; then
1,000 orders over HTTP on loopback (a new TCP connection per order, since the
server speaks HTTP/1.0; the response carries the full account state).

| Measurement | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|
| All eight checks + fill, in process | **72 µs** | 85 µs | 127 µs | 752 µs |
| Order round trip over HTTP | 1.6 ms | 1.8 ms | 2.3 ms | 4.9 ms |

Throughput in process: **~14,600 orders/s** on one core.

## 4. Procedure
```bash
python3 bench/bench.py                 # everything (~90 s)
python3 bench/bench.py engine          # just one section: engine | run | server | live | gateway
```
For live, end-to-end numbers: start the server, open Live market, connect to
BTC-USD, and read the latency panel after a minute.

## 5. Interpretation

**Simulation cost is linear and small.** Throughput stays at ~180k bars/s
from 10 to 1,000 years, so cost per day is constant (O(1) strategy updates,
O(lookback) vol estimate). Checking every accounting invariant every day costs
under 7%, so we leave the checks on by default: correctness is cheap here.

**The bottleneck is the luck test, not the backtest.** It is 97% of a run and
scales linearly with shuffles, because each shuffle re-scores 2,500 days in
pure Python. Next step: vectorize with NumPy (a permutation matrix times
returns), or stop early once the p-value is clearly above or below 0.05.

**The result store makes repeat questions nearly free.** A cached run returns
in ~2 ms versus ~900 ms to compute, a ~450× speed-up, and it is guaranteed
identical because the ID is the hash of config + data + engine version. With
8 concurrent clients, throughput is 455 req/s and latency rises to 16 ms p50.

**Live: our part of the latency is about 1 ms; the internet is the rest.**
From a trade arriving on the socket to the update being in the browser's
socket takes 0.88 ms p50, of which the risk recalculation is ~0.13 ms. Two
choices keep it there: the stream loop *waits on a condition variable* that
the socket reader signals the instant a trade lands (no polling interval),
and Nagle's algorithm is turned off on the HTTP connection so each update is
sent immediately instead of being held to fill a packet. Bursts are batched
to at most one update per 16 ms (one 60 Hz screen frame): no screen can show
updates faster, and it keeps the browser from being flooded. (It was 100 ms
in the first version; a trade can now wait at most 16 ms in a burst.) For stocks, Yahoo's free
data is ~1 minute delayed and polled every 20 s, so the honest stock latency
is "about a minute", and the app says so rather than pretending otherwise.
Each hit re-reads and re-parses a ~110–130 KB JSON file under Python's GIL, so the
next fix is an in-memory LRU cache of hot results.

**What would fail first at scale:** (1) CPU-bound luck tests block a server
thread for ~1 s each, so many simultaneous *new* runs queue up; a worker pool
(processes, not threads) is the fix. (2) The result store grows without bound
and needs eviction or object storage. (3) Multi-asset portfolios would multiply
work by the number of symbols; the per-symbol loop is the place to vectorize.

**Correctness/performance tradeoff we chose:** money in integer cents and
per-day invariant checks cost a few percent of speed. In exchange, the books
balance exactly and any accounting bug fails loudly on the day it happens.

**Pre-trade checks cost about 72 µs; HTTP costs the rest.** All eight checks
and the fill run in ~72 µs median in pure Python, even though money is exact
fractions (slower than floats, but it makes "the books reconcile exactly" a
checkable fact). Over HTTP the round trip is 1.6 ms, so ~95% of an order's
time is connection setup and JSON, not risk logic. The next steps, in order of
payoff: keep-alive connections (HTTP/1.1) or a WebSocket for order entry, then
integer micro-dollars instead of fractions if checks ever dominate.
