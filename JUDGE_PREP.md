# Judge Q&A prep (for you, not for the judges)

## 30-second pitch
"RiskLens is a backtester built around risk. You give it a simple strategy and
real stock prices. It shows how risky the strategy really is (worst drawdown,
Value-at-Risk, volatility) and what risk rules like a drawdown circuit breaker
would have saved you. Its core promise is that you can trust the numbers: it
physically can't see the future, the money balances to the cent every day,
and every result is reproducible from a hash. It even checks whether its
own risk estimate was right, the way banks must. And it runs live: every order
goes through eight pre-trade risk checks against real prices, with a kill
switch. We proved it with 175 tests, and when we planted 19 bugs, the tests
caught all 19."

## Demo (3 minutes)
1. Double-click **Start RiskLens.bat** in the project folder. It opens
   **http://127.0.0.1:8765/** in the browser by itself (keep its black window open).
2. **Live market (open with this, it's the most alive):** click **BTC** and
   Connect. "These are real Bitcoin trades from Coinbase, pushed to us the
   moment they happen. On every trade we recompute the risk: the volatility
   target says hold X% right now, and we're Y% below the session high against
   a 15% breaker." Point at the latency panel: "We measure our own latency.
   Our server adds about a millisecond; the rest is the internet."
   *Judging is on a weekend: US stocks are closed, so use crypto. SPY will
   show Friday's session and then "No new prices", which is correct.*
   *If the Wi-Fi is bad:* switch the source to **Simulated · offline** (it is
   clearly labeled SIMULATED).
3. **Risk gateway:** (keep BTC connected) buy 0.1 BTC: "Approved: all eight
   checks passed in about 70 microseconds." Then type a huge size: "Rejected:
   the order is too big, breaks the position limit and the exposure limit,
   and it tells you exactly why." Press **Kill switch**: "It sells everything
   and blocks new buys until a person re-arms it. It also trips by itself if
   the account falls 15% from its peak."
4. **Overview:** "Every stock we downloaded, with its current risk status.
   And it works on any stock": type a ticker the judge picks (say **NVDA**) in
   the search box and press Enter. "First time it downloads the history,
   freezes it with a fingerprint, and analyzes it."
   *The first start downloads SPY, QQQ, AAPL, MSFT, NVDA and TSLA and saves
   them, so start it once before judging; then the demo never depends on the
   venue Wi-Fi.*
5. **Analyze:** pick SPY, Trend 50/200, 15% volatility target and 15% breaker,
   then Run. Press **→** to flip to the next stock and **←** to come back:
   "Saved results come back in about 20 milliseconds, because every run is
   stored under its fingerprint." Walk through Summary → Drawdowns → Stress tests (COVID crash) →
   **Key findings**, then **Risk model check**: "We forecast tomorrow's risk
   six ways and grade every model with the tests banks must run. VaR from the
   account's own history was beaten 125 times when 56 were expected; sizing
   risk from today's position × the stock's risk passes." Then **What each
   rule does**: "The volatility target did the work. The circuit breaker
   alone actually made the worst fall deeper." Then **Robust or lucky?**:
   "25 nearby settings, and both halves of the period." Click **Open full
   report** (top right of the report) to show it full screen.
6. **Replay:** press Start on Fast. Talk while it runs: "This is the
   engine replaying 15 years one day at a time. Watch the teal line stay
   shallower in the drawdown chart. When the breaker fires, it shows up here
   in the alert feed." Pause during the COVID crash (early 2020) to show it.
7. **History:** "Every run is saved and reproducible from its ID."
8. If asked about correctness: `python -m pytest -q` (175 passed) and
   `./scripts/mutation_check.sh` (19/19).

## Likely questions

**"How low is your latency?"**
"Our part is under a millisecond: 0.64 ms median from a trade arriving to the
update leaving for the browser, measured with `bench/bench.py live`. The
screen shows the full chain live, including the internet hop from Coinbase.
We don't poll: the socket reader wakes the risk loop the instant a trade
lands, and we turned off Nagle's algorithm so updates aren't held back. For
stocks we only have Yahoo's free feed, which is a minute delayed, and we say
so on screen."

**"How do you know your VaR is right?"**
"We backtest it. Each day we forecast VaR from the previous 250 days only,
then check if the next day's loss was bigger. A 95% VaR should be beaten on
about 5% of days (Kupiec test), and the breaches shouldn't bunch together
(Christoffersen test). With risk rules our count passes, but breaches cluster
in volatile periods, a known weakness of historical VaR."

**"Your VaR said accurate before, now it fails. Which is it?"**
"Our first check counted every day, including days in cash, where you can't
lose money, so it looked accurate. Graded only on days the strategy held
stock, VaR from the account's own history was beaten more than twice as
often as it should be. We fixed the measurement, not the number: risk now
comes from today's position × the stock's risk, which is how risk desks do
it, and that passes."

**"Is the strategy robust?"**
"We re-ran it with 25 nearby settings and split the period in half. The
trend strategy's result doesn't depend on the exact numbers and held up in
both halves; the momentum strategy's Sharpe went from 0.9 to 0.0 between
halves, so we'd call that luck of the period."

**"What does the risk gateway check?"**
Eight things, in order: valid order, a fresh live price (no price, no trade),
kill switch, order size (fat finger), order rate, cash (or no short selling),
position limit, and exposure limit, which tightens automatically when
volatility rises (vol target ÷ current vol). "We prove with random order
sequences that no approved order ever breaks a limit, and the paper account
reconciles exactly, using exact fractions instead of floats."

**"Why Coinbase and not TradingView?"**
TradingView has no public data API (only chart widgets), and scraping it
breaks its terms. Coinbase publishes a free, real-time, public WebSocket.

**"What if the live feed drops?"**
The app says so in the alert feed, reconnects every few seconds, and refills
the missed minutes from Coinbase's own 1-minute candles, so the chart and
volatility have no hole. Tested with a fake socket.

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
Not the backtest. Simulating 10 years takes 9 ms (~200k bars/s). The luck
test is 55% of a full analysis (1,000 shuffles in pure Python) and the deep
dive's 29 extra backtests another 31%. Next: NumPy for the luck test, or stop
it early once the p-value is clearly decided, and run the robustness grid in
parallel processes.

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
~200,000 bars/s · 10-year run simulates in 9 ms · luck test 55% of a run ·
cached result 3 ms · full analysis 0.92 s · live pipeline 0.64 ms p50 · pre-trade checks 63 µs p50 ·
175 tests · 19/19 mutations caught · money in integer cents (backtest) and exact fractions (gateway).
