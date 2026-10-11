# Judge Q&A prep (for you, not for the judges)

## 30-second pitch
"RiskLens is a backtester built around risk. You give it a simple strategy and
real stock prices. It shows how risky the strategy really is (worst drawdown,
Value-at-Risk, volatility) and what risk rules like a drawdown circuit breaker
would have saved you. Its core promise is that you can trust the numbers: it
physically can't see the future, the money balances to the cent every day,
and every result is reproducible from a hash. We proved those with 56 tests,
and when we planted 11 bugs, the tests caught all 11."

## Demo (2 minutes)
1. `./scripts/demo.sh AAPL` (or plain `./scripts/demo.sh` for synthetic data).
2. Point at the table: same strategy, without vs with risk rules. Max
   drawdown and volatility drop a lot; return drops a little.
3. Show the second submit: `(cached)`, same run IDs, about 2 ms.
4. Open the HTML report: equity curve, drawdown chart, risk events (the dates
   the breaker tripped), luck-test p-value.

## Likely questions

**"Which invariant matters most, and how do your tests try to break it?"**
No look-ahead. The test takes a random market, picks a day k, and **replaces
every price after day k with different prices**. If any decision up to day k
used future data, it would change. It never does. On top of that, the strategy
only receives a list containing bars up to today, and trades decided at the
close fill at the next day's open. Then show `./scripts/mutation_check.sh`:
the first mutation lets the strategy see tomorrow's bar, and the tests catch it.

**"How do you know the accounting is right?"**
Money is integer cents, so there is no floating-point drift. Every day the
engine checks `cash = start − buys + sells − fees` exactly. Separately, a test
rebuilds cash and shares from the trade list alone and compares, over
hundreds of random markets with gaps, fees and slippage.

**"Does your strategy have an edge?"**
"Probably not, and the tool says so." The luck test shuffles the strategy's
timing 1,000 times; a p-value around 0.4–0.5 means random timing does as
well. We're not selling a strategy. We built the tool that tells you honestly
whether one is any good, and how much risk it carries.

**"Why a server?"**
On a quant team many people run backtests. One server owns the frozen data and
an immutable result store. The same question gets the same stored answer and
the same ID, and nobody can quietly re-run on edited data (the server refuses
to start if a data file's hash changed).

**"What's the bottleneck?"**
Not the backtest. Simulating 10 years takes 12 ms (~180k bars/s). The luck
test is 97% of the time: 1,000 shuffles in pure Python. Next: NumPy, or stop
early once the p-value is clearly decided.

**"Why did max drawdown with risk rules exceed the 15% breaker?"**
The breaker limits each losing episode. After the cooldown it re-arms from the
new level, so two separate 15% episodes compound to about 28%. We show that
instead of hiding it. An absolute "never below X" floor would be a one-line
variant.

**"What's Value-at-Risk / Expected Shortfall?"**
VaR 95%: on the worst 5% of days, you lose at least this much (we use the
k-th worst day). Expected Shortfall: the average loss on those worst 5% of
days. It answers "when it's bad, how bad?"

**"What's volatility targeting?"**
Hold less stock when the market is wild and more when it's calm, aiming for
steady risk (e.g. 15%/yr). Size = target vol ÷ recent realized vol, capped at 100%.

**"What didn't you do?"** (have this ready; honesty scores points)
Daily bars only, one stock, long-only, a simple fill model, survivorship bias
in Yahoo data, and a luck test that ignores volatility clustering.

**A good story:** "Our tests caught a real floating-point bug: `(1 − 0.95) × 40`
is 2.0000000000000018 in floating point, so VaR used the 3rd-worst day instead
of the 2nd. That's exactly the kind of silent error that makes backtests lie."

## Numbers to know
~180,000 bars/s · 10-year run simulates in 12 ms · luck test 97% of time ·
cached result 2 ms · 56 tests · 11/11 mutations caught · money in integer cents.
