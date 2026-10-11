# RiskLens performance report

Follows Lucera's template: environment, workload, metric, procedure,
interpretation. Every number comes from `python3 bench/bench.py`; raw JSON
is in `bench/results/`. All tables below were measured on the same machine
in one session.

## 1. Environment
| | |
|---|---|
| CPU | Intel Xeon @ 2.10 GHz, **2 vCPU** (cloud VM) |
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
  validate CSV, simulate, metrics, the six-model risk check, the deep dive
  (29 extra backtests for rule attribution and the robustness grid), and a
  1,000-shuffle luck test. Best of 5.
* **Luck test:** 2,519 days, 100 / 1,000 / 5,000 shuffles.
* **Server:** 10-year dataset. *Cold:* 20 distinct configs, each computed.
  *Hot:* one config requested 500 times by 1 client, then 2,000 times by 8
  concurrent clients (served from the result store).
* **Live market:** 200 BTC-USD trades, one every 120 ms, fed into the real
  `CoinbaseProvider` through an in-memory fake socket over a session of 300
  backfilled 1-minute bars; an SSE client in the same process reads the
  updates over loopback.
* **Risk gateway:** 10,000 orders (alternating buy/sell 0.01 BTC) through all
  eight checks and the simulated fill on one paper account; then 1,000 orders
  over HTTP (a new TCP connection per order, since the server speaks
  HTTP/1.0; each response carries the full account state).

## 3. Results

### Engine throughput
| Data | Bars | Invariants checked | Time | Bars/s |
|---|---:|---|---:|---:|
| 10 years | 2,520 | yes | 12.8 ms | **196,258** |
| 10 years | 2,520 | no | 11.8 ms | 213,098 |
| 100 years | 25,200 | yes | 87 ms | 289,525 |
| 100 years | 25,200 | no | 82 ms | 307,400 |
| 1,000 years | 252,000 | yes | 1.01 s | 249,997 |
| 1,000 years | 252,000 | no | 0.97 s | 259,491 |

* Peak traced memory for 100 years: 4.3 MB (~169 bytes per bar).
* Same inputs twice gave the same result fingerprint (`deterministic_fingerprint_match: true`).

### Where a full 10-year run spends its time
| Stage | ms | Share |
|---|---:|---:|
| Load + validate CSV | 7.5 | 1% |
| Simulate (engine) | 8.9 | 1% |
| Risk model check (6 VaR models × 2 levels, graded) | 113.9 | 12% |
| Deep dive (tails, 4 rule variants, 25-setting grid + buy & hold, sources, regimes) | 282.9 | 31% |
| Luck test (1,000 shuffles) | 505.3 | **55%** |
| **Total** | 920.5 | |

### Luck-test scaling
| Shuffles | Seconds |
|---:|---:|
| 100 | 0.05 |
| 1,000 | 0.54 |
| 5,000 | 2.71 |

### Server (server on CPU 0, clients on CPU 1)
| Request | p50 | p90 | p99 | max | Throughput |
|---|---:|---:|---:|---:|---:|
| New run, computed (n=20) | 1,012 ms | 1,154 ms | 1,261 ms | 1,261 ms | |
| Cached result, 1 client (n=500) | **3.1 ms** | 4.1 ms | 5.6 ms | 7.8 ms | |
| Cached result, 8 clients (n=2,000) | 21.5 ms | 28.2 ms | 36.0 ms | 1,029 ms | **329 req/s** |

Server peak RSS: 34 MB. Server stats confirmed 20 runs computed and 2,500
cache hits. The one 1 s outlier among 2,000 hot requests is a single stall,
probably the OS or a garbage collection; p99 stays at 36 ms.

### Live market pipeline
| Stage | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|
| Trade message arrives → risk update read by the client | **0.64 ms** | 0.82 ms | 1.65 ms | 2.52 ms |
| Risk snapshot inside that (300 bars) | 0.13 ms | 0.16 ms | 0.39 ms | 1.08 ms |
| Risk snapshot alone, 1,440 bars (a full day) | 0.20 ms | 0.31 ms | 0.37 ms | 0.82 ms |

**Not included, and measured live in the app instead:** the internet hop
from Coinbase to your machine ("Exchange → server", Coinbase's trade
timestamp vs. your clock) and browser rendering. The app's latency panel
shows medians of the last 40 updates for exchange → server, risk
calculation, server → browser, and trade → your screen.

### Pre-trade risk gateway
| Measurement | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|
| All eight checks + fill, in process | **63 µs** | 103 µs | 159 µs | 831 µs |
| Order round trip over HTTP | 1.57 ms | 2.07 ms | 2.48 ms | 2.91 ms |

Throughput in process: **~14,400 orders/s** on one core.

## 4. Procedure
```bash
python3 bench/bench.py                 # everything (~2 minutes)
python3 bench/bench.py engine          # one section: engine | run | server | live | gateway
```
For live, end-to-end numbers: start the server, open Live market, connect to
BTC-USD, and read the latency panel after a minute.

## 5. Interpretation

**Simulation cost is linear and small.** Throughput stays around 200,000 to
300,000 bars/s from 10 to 1,000 years, so the cost per day is constant (O(1)
strategy updates, O(lookback) vol estimate). Checking every accounting
invariant every day costs 3–8%, so the checks stay on by default:
correctness is cheap here.

**The analysis costs more than the backtest, by design.** Simulating 10 years
takes 9 ms; asking the questions a risk manager asks takes the rest: the risk
model check (114 ms), the deep dive (283 ms, mostly 29 extra backtests for
rule attribution and the robustness grid) and the luck test (505 ms, 55% of
the run). The luck test is the bottleneck: each shuffle re-scores 2,500 days
in pure Python. Next steps: vectorize it with NumPy, stop early once the
p-value is clearly above or below 0.05, and run the robustness grid's
independent backtests in parallel processes.

**The result store makes repeat questions nearly free.** A cached run returns
in ~3 ms versus ~1 s to compute, a ~330× speed-up, and it is guaranteed
identical because the ID is the hash of config + data + engine version. That
is why switching back to a stock you already analyzed takes ~20 ms in the
app. Each hit re-reads and re-parses the stored JSON under Python's GIL (it
grew with the deep dive, which is why the cached p50 went from 2 ms to 3 ms),
so the next fix is an in-memory LRU cache of hot results.

**Live: our part of the latency is under a millisecond; the internet is the
rest.** From a trade arriving on the socket to the update being in the
browser's socket takes 0.64 ms p50, of which the risk recalculation is
~0.13 ms. Two choices keep it there: the stream loop *waits on a condition
variable* that the socket reader signals the instant a trade lands (no
polling interval), and Nagle's algorithm is turned off on the HTTP connection
so each update is sent immediately instead of being held to fill a packet.
Bursts are batched to at most one update per 16 ms (one 60 Hz screen frame):
no screen can show updates faster, and it keeps the browser from being
flooded. For stocks, Yahoo's free data is ~1 minute delayed and polled every
20 s, so the honest stock latency is "about a minute", and the app says so.

**Pre-trade checks cost about 63 µs; HTTP costs the rest.** All eight checks
and the fill run in ~63 µs median in pure Python, even though money is exact
fractions (slower than floats, but it makes "the books reconcile exactly" a
checkable fact). Over HTTP the round trip is 1.6 ms, so ~95% of an order's
time is connection setup and JSON, not risk logic. The next steps, in order of
payoff: keep-alive connections (HTTP/1.1) or a WebSocket for order entry, then
integer micro-dollars instead of fractions if checks ever dominate.

**What would fail first at scale:** (1) CPU-bound runs block a server thread
for ~1 s each, so many simultaneous *new* runs queue up; a worker pool
(processes, not threads) is the fix. (2) The local result store grows without
bound (the public mode caps it). (3) Multi-asset portfolios would multiply the
work by the number of symbols; the per-symbol loop is the place to vectorize.

**Correctness/performance tradeoff we chose:** money in integer cents (and
exact fractions in the gateway) and per-day invariant checks cost a few
percent of speed. In exchange, the books balance exactly and any accounting
bug fails loudly on the day it happens.
