# Devpost submission text (copy/paste)

**Project name:** RiskLens

**Tagline:** Risk management for trading strategies: tested through real
crashes, checked against live prices, and proven correct by its own tests.

**Track:** Maverick · **Superlative:** Lucera Trading Systems Challenge

**In one line (put this first):** A backtesting engine that prevents
look-ahead bias and supports reproducible runs, built around risk controls
(pre-trade risk checks, position limits, exposure monitoring and a kill
switch), and also running live on real prices.

## Inspiration
Most backtests you see online look amazing and are quietly wrong. They peek
at future prices, fill trades at prices nobody could have gotten, ignore
costs, and report returns without risk. In 2012 Knight Capital lost about
$440 million in 45 minutes because nothing stopped a broken trading program.
We wanted a tool that is honest first: one that measures risk properly,
controls it before orders go out, and can prove its own numbers.

## What it does
* **Analyze:** runs a strategy over historical daily prices (real data from
  Yahoo Finance, frozen as a hashed snapshot, or seeded synthetic data)
  **twice, without and with risk rules** (volatility targeting, exposure cap,
  drawdown circuit breaker), and compares max drawdown, volatility, Sharpe,
  Sortino, VaR, Expected Shortfall and stress tests through real crashes.
* **VaR check:** backtests the risk model itself, the way banks must. Each
  day's VaR is forecast from the previous 250 days only; then the Kupiec test
  asks whether the number of breaches is right, the Christoffersen test asks
  whether they cluster, and the Basel traffic light grades the 99% VaR.
* **Luck test:** shuffles the strategy's timing 1,000 times and reports a
  p-value, so nobody mistakes luck for skill.
* **Live market:** every trade from Coinbase's public WebSocket (24/7, no key;
  stocks from Yahoo, delayed). On each update: how much the volatility target
  lets you hold right now, how close the breaker is to firing, 60-minute
  volatility and 1-minute VaR, alerts, and **a measured latency breakdown on
  screen** (exchange → server, risk calculation, server → browser, trade →
  your screen).
* **Risk gateway:** a paper account where every order must pass **eight
  pre-trade checks** against the live price: fresh price (fail closed), kill
  switch, fat-finger order size, order rate, cash, no short selling, position
  limit, and an exposure limit that tightens when volatility rises. A **kill
  switch** can be pressed by hand and trips by itself on a drawdown: it sells
  everything and blocks buys until a person re-arms it.
* **Replay:** streams 10+ years through the engine day by day like a risk
  desk screen; plus **Overview** (risk light per stock) and **History**.
* **Any stock, one click:** **+ Add stock** downloads a ticker from inside
  the app (frozen and fingerprinted, no restart), and a stock bar switches
  every page in one click or with the ← → keys. Saved results come back in
  about 20 ms; presets and period chips re-run instantly.

## How we built it
The engine, server and app are pure Python standard library. A run **server**
owns the data snapshots and an immutable result store; a CLI **client** and
the browser app talk to it over HTTP and Server-Sent Events. Each run's ID is
the SHA-256 of (config, data hash, engine version), so the same question
always returns the same stored answer.

For live data, a reader thread holds Coinbase's WebSocket, builds trades into
1-minute bars, and wakes the stream loop the instant a trade lands; the loop
recomputes risk, feeds the price to the risk gateway, and pushes the update
to the browser with Nagle's algorithm off. If the feed drops, it reconnects
and refills the missed minutes from Coinbase's own candles.

It also runs as a public website (`--public`): per-visitor rate limits,
capped live streams, a bounded store, and one paper account per browser.

## Correctness evidence
* **No look-ahead:** Hypothesis replaces every price after day k with
  different values and proves nothing decided up to day k changes; the same
  is proven for every VaR forecast.
* **Books balance exactly:** backtest money is integer cents, gateway money is
  exact fractions; cash, positions and fees reconcile after every trade.
* **No approved order ever breaks a limit:** Hypothesis runs random
  sequences of orders, price moves, limit changes and kill switch presses,
  and checks every limit after every step.
* **The live replay matches the backtest to the cent.**
* **143 tests**, run on Python 3.11–3.13 in CI. A mutation script plants **17
  realistic bugs** (look-ahead, ignored kill switch, stale prices, wrong
  p-values…) and the tests catch **17/17**. The tests also caught real bugs
  during the hackathon: a floating-point off-by-one in VaR, and a crash when a
  price rounds to $0.00.

## Performance (2 vCPU VM)
* Backtest engine: ~**180,000 bars/s** with all invariants checked; a 10-year
  run simulates in **12 ms** (the luck test is 97% of run time, the measured
  bottleneck).
* Live: a trade reaches the browser's socket **0.88 ms** p50 after it arrives
  at our server. The rest is the internet, which the app measures live.
* Risk gateway: all eight pre-trade checks in **72 µs** p50 (~14,600 orders/s);
  1.6 ms round trip over HTTP.

## Challenges
Low latency without lying about it: the free stock feed is a minute delayed,
so we added Coinbase's real-time feed and put a measured latency breakdown on
screen instead of claiming a number. And making tests that really bite: our
mutation script twice found a test too weak to catch a planted bug, and we
fixed both.

## What we learned
The backtest loop is the easy part. The hard part is being sure it's right:
defining "no look-ahead" precisely enough to test, keeping money exact, and
checking the risk estimate itself. Our own VaR check showed that historical
VaR breaches cluster in volatile periods, which is exactly why the
volatility target and kill switch exist.

## What's next
A faster-reacting VaR model (EWMA or GARCH) graded by the same VaR check,
multi-asset portfolios with a portfolio VaR limit, walk-forward testing, and
a NumPy luck test.

## Built with
python, http.server, server-sent-events, websocket-client, coinbase-exchange-api,
pytest, hypothesis, yfinance, github-actions

## Disclosure
All code was written during HackRU with help from an AI coding assistant
(Claude), and was reviewed, run and tested by the team. Runtime uses only the
Python standard library, plus websocket-client for the Coinbase live feed;
pytest and Hypothesis are for tests; yfinance downloads data and provides the
delayed stock feed. Price data: Yahoo Finance (read-only, not redistributed),
Coinbase Exchange public market data (live, not stored), and seeded synthetic
data. Everything is simulated: no real orders, money or credentials.
