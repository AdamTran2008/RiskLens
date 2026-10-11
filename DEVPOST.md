# Devpost submission text (copy/paste)

**Project name:** RiskLens

**Tagline:** A backtester you can trust: it can't peek at the future, its
books balance to the cent, and it shows exactly what risk rules buy you.

**Track:** Maverick · **Superlative:** Lucera Trading Systems Challenge

## Inspiration
Most backtests you see online look amazing and are quietly wrong. They peek
at future prices, fill trades at prices nobody could have gotten, ignore
costs, and report returns without risk. In 2012 Knight Capital lost about
$440 million in 45 minutes because nothing stopped a broken trading program.
We wanted a tool that is honest first: one that measures risk properly,
shows the effect of risk controls, and can prove its own numbers.

## What it does
* Runs a trading strategy over historical daily prices (real data from Yahoo
  Finance, frozen as a hashed snapshot, or seeded synthetic data).
* Runs it twice, **without and with risk rules** (volatility targeting,
  exposure cap, drawdown circuit breaker), and compares max drawdown,
  volatility, Sharpe, Sortino, 1-day VaR and Expected Shortfall, and worst day.
* A **luck test** shuffles the strategy's timing 1,000 times to check whether
  it beats random timing, and reports a p-value.
* Produces an HTML report with equity and drawdown charts, a metrics table,
  every risk-rule event, and the run's reproducibility manifest.

* **Live market:** real prices as they happen, with live feedback from the
  same risk rules. Crypto streams every trade from Coinbase's public
  WebSocket (24/7, no key); stocks come from Yahoo Finance (delayed). On each
  update it shows how much the volatility target lets you hold right now, how
  close the drawdown breaker is to firing, 60-minute volatility and 1-minute
  VaR, raises alerts on threshold crossings, and **measures its own
  latency on screen**: exchange → server, risk calculation, server → browser,
  and trade → your screen.
* A web app with five screens: **Overview** (watchlist with a risk status
  light per stock), **Live market**, **Analyze** (sliders for the strategy
  and risk rules), **Replay** (history streamed through the engine day by
  day, like a risk desk screen) and **History** (every saved run).

## How we built it
The engine, server and app are Python standard library only. A run **server** owns the data snapshots and an
immutable result store; a CLI **client** submits runs and fetches reports.
Each run's ID is the SHA-256 of (config, data hash, engine version), so the
same question always returns the same stored answer, and changed data or code
can never be silently mixed with old results.

For live data, a reader thread holds Coinbase's WebSocket, builds trades into
1-minute bars, and wakes the stream loop the instant a trade lands; the loop
recomputes the risk snapshot and pushes it to the browser over Server-Sent
Events with Nagle's algorithm off. If the feed drops, it reconnects and
refills the missed minutes from Coinbase's own candles.

## Correctness evidence
* **No look-ahead:** Hypothesis replaces every price after day k with
  different values and proves nothing decided up to day k changes. Orders
  decided at the close fill at the next open.
* **Books balance to the cent:** money is integer cents. Invariants are
  checked every day, and an independent ledger replay rebuilds cash and
  shares from the trade list over hundreds of random markets.
* **Risk rules always hold**, and every run is reproducible.
* **The live replay matches the backtest exactly:** streamed values equal the
  batch engine's to the cent, and pausing and resuming changes nothing.
* **The live feed never invents or drops prices silently:** tested against a
  fake socket that speaks Coinbase's message format (bar building, reconnect
  and gap fill, bad symbols, end-to-end push).
* **82 tests**. A mutation script plants 11 realistic bugs and the tests
  catch **11/11**. The tests also caught two real bugs during the hackathon:
  a floating-point off-by-one in VaR, and a crash when a price rounds to $0.00.

## Performance (2 vCPU VM, pinned cores)
* ~**180,000 bars/s** simulated with all invariants checked; flat from 10 to 1,000 years.
* A 10-year backtest simulates in **12 ms**; the luck test is 97% of run time
  (the measured bottleneck, next to optimize).
* Cached results served in **2 ms** p50; 455 req/s with 8 concurrent clients.
* **Live:** a trade reaches the browser's socket **0.88 ms** p50 after it
  arrives at our server (risk recalculation ~0.13 ms). The rest is the
  internet, which the app measures and shows live.

## What we learned
The backtest loop is the easy part. The hard part is being sure it's right:
defining "no look-ahead" precisely enough to test, keeping money exact, and
measuring risk honestly instead of just returns.

## Challenges
Low latency without lying about it. The free stock feed is a minute
delayed, so we added Coinbase's real-time crypto feed and put a measured
latency breakdown on screen instead of claiming a number.

## What's next
NumPy luck test, multi-asset portfolios with a portfolio VaR limit,
walk-forward testing, and an in-memory result cache.

## Built with
python, http.server, server-sent-events, websocket-client, coinbase-exchange-api, pytest, hypothesis, yfinance

## Disclosure
All code was written during HackRU with help from an AI coding assistant
(Claude), and was reviewed, run and tested by the team. Runtime uses only the
Python standard library; pytest and Hypothesis are for tests; yfinance is used
to download data and for the delayed stock feed; websocket-client for the
Coinbase live feed. Price data: Yahoo Finance (read-only, not redistributed),
Coinbase Exchange public market data (live, not stored), and seeded synthetic
data. Everything is simulated: no real
orders, money or credentials.
