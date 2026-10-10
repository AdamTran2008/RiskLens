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

## How we built it
Python standard library only. A run **server** owns the data snapshots and an
immutable result store; a CLI **client** submits runs and fetches reports.
Each run's ID is the SHA-256 of (config, data hash, engine version), so the
same question always returns the same stored answer, and changed data or code
can never be silently mixed with old results.

## Correctness evidence
* **No look-ahead:** Hypothesis replaces every price after day k with
  different values and proves nothing decided up to day k changes. Orders
  decided at the close fill at the next open.
* **Books balance to the cent:** money is integer cents. Invariants are
  checked every day, and an independent ledger replay rebuilds cash and
  shares from the trade list over hundreds of random markets.
* **Risk rules always hold**, and every run is reproducible.
* **55 tests**. A mutation script plants 11 realistic bugs and the tests
  catch **11/11**. The tests also caught two real bugs during the hackathon:
  a floating-point off-by-one in VaR, and a crash when a price rounds to $0.00.

## Performance (2 vCPU VM, pinned cores)
* ~**180,000 bars/s** simulated with all invariants checked; flat from 10 to 1,000 years.
* A 10-year backtest simulates in **12 ms**; the luck test is 97% of run time
  (the measured bottleneck, next to optimize).
* Cached results served in **2 ms** p50; 455 req/s with 8 concurrent clients.

## What we learned
The backtest loop is the easy part. The hard part is being sure it's right:
defining "no look-ahead" precisely enough to test, keeping money exact, and
measuring risk honestly instead of just returns.

## What's next
NumPy luck test, multi-asset portfolios with a portfolio VaR limit,
walk-forward testing, and an in-memory result cache.

## Built with
python, http.server, pytest, hypothesis, yfinance

## Disclosure
All code was written during HackRU with help from an AI coding assistant
(Claude), and was reviewed, run and tested by the team. Runtime uses only the
Python standard library; pytest and Hypothesis are for tests; yfinance is used
once to download data. Price data: Yahoo Finance (read-only, not
redistributed) and seeded synthetic data. Everything is simulated: no real
orders, money or credentials.
