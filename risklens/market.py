"""Live market mode: watch real prices as they arrive and give live risk feedback.

On every price change the monitor recomputes a live risk snapshot:

  * price and change since the session open
  * drawdown from the session high
  * rolling 60-minute volatility, annualized
  * 1-minute historical VaR 95% over the last 240 minutes
  * live feedback from the risk rules: the exposure volatility targeting
    would allow right now, and how close the drawdown breaker is to firing

Three providers:
  * CoinbaseProvider: real-time crypto prices pushed trade by trade over
    Coinbase Exchange's public WebSocket feed (no account or API key). The
    session is backfilled from Coinbase's public 1-minute candles.
  * YahooProvider: Yahoo Finance 1-minute bars via yfinance, polled. Works for
    stocks, but Yahoo's data is delayed and only updates when polled.
  * SimProvider: a clearly labeled simulated feed for offline demos and tests.

US stock markets trade 9:30-16:00 New York time on weekdays. Outside those
hours a stock's feed stops changing and the monitor reports it as stale.
Crypto pairs such as BTC-USD trade around the clock.

Latency is measured, not assumed. Every update carries:
  feed_ms     exchange trade timestamp -> our server received it (median of
              the last 50 trades; uses the PC clock, so a few ms of clock
              error is normal)
  compute_ms  time to recompute the risk snapshot
  sent_ms     server clock when the update was sent; the browser subtracts
              this from its own clock to get server -> browser time
  last_trade_ms  exchange time of the newest trade, for end-to-end latency
"""
from __future__ import annotations

import collections
import json
import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone

VOL_WINDOW = 60          # minutes
VAR_WINDOW = 240         # minutes
MIN_PER_YEAR_CRYPTO = 365 * 24 * 60
MIN_PER_YEAR_STOCK = 252 * 390


class ClientGone(Exception):
    pass


class FeedError(Exception):
    """A data feed problem. ``fatal`` means retrying cannot help (bad symbol,
    missing package), so the stream reports it and stops."""

    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal


def is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith(("-USD", "-USDT", "-USDC", "-EUR", "-GBP", "-BTC"))


# ---------------------------------------------------------------- providers
class YahooProvider:
    name = "Yahoo Finance (yfinance, delayed)"
    simulated = False
    push = False

    def bars(self, symbol: str):
        """Today's 1-minute bars as [(iso_time, open, high, low, close, volume)]."""
        try:
            import yfinance as yf
        except ImportError:
            raise FeedError("yfinance is not installed: pip install yfinance", fatal=True)
        try:
            df = yf.Ticker(symbol).history(period="1d", interval="1m", auto_adjust=False,
                                           actions=False)
        except Exception as e:                        # network, rate limit, bad symbol
            raise FeedError(f"Yahoo Finance request failed: {e}")
        if df is None or df.empty:
            raise FeedError(f"no intraday data for {symbol} (check the symbol)")
        out = []
        for ts, row in df.iterrows():
            vals = [float(row[k]) for k in ("Open", "High", "Low", "Close")]
            if any(v != v or v <= 0 for v in vals):
                continue
            o, h, l, c = vals
            out.append((ts.isoformat(), o, max(h, o, c), min(l, o, c), c, float(row["Volume"])))
        if not out:
            raise FeedError(f"no valid intraday bars for {symbol}")
        return out


class SimProvider:
    """Simulated minute bars: one new bar every ``seconds_per_bar`` of wall time,
    with occasional volatility bursts so alerts can be demonstrated offline."""
    name = "Simulated feed (not real prices)"
    simulated = True
    push = False

    def __init__(self, seed=11, seconds_per_bar=2.0, history=180, clock=time.time):
        self.seed, self.spb, self.history, self.clock = seed, seconds_per_bar, history, clock
        self.t0 = clock()

    def bars(self, symbol: str):
        n = self.history + int((self.clock() - self.t0) / self.spb)
        rng = random.Random(f"{self.seed}:{symbol}")
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        px = 100.0 + (sum(map(ord, symbol)) % 400)
        out = []
        for i in range(n):
            burst = 4.0 if (i // 90) % 4 == 3 else 1.0          # periodic turbulence
            sig = 0.0009 * burst
            o = px
            c = o * math.exp(rng.gauss(-0.00002 * burst, sig))
            h = max(o, c) * math.exp(abs(rng.gauss(0, sig / 2)))
            l = min(o, c) * math.exp(-abs(rng.gauss(0, sig / 2)))
            out.append(((start + timedelta(minutes=i)).isoformat(), o, h, l, c, 1000.0))
            px = c
        return out


def _ws_connect(url):
    try:
        import websocket                               # the websocket-client package
    except ImportError:
        raise FeedError("the Coinbase feed needs one package: pip install websocket-client",
                        fatal=True)
    return websocket.create_connection(url, timeout=30, enable_multithread=True)


def _fetch_candles(product, timeout=10.0):
    """Coinbase public REST: up to 300 one-minute candles, newest first, as
    [time, low, high, open, close, volume]."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(CoinbaseProvider.CANDLES_URL.format(product),
                                 headers={"User-Agent": "RiskLens/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rows = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise FeedError(f"Coinbase has no trading pair called {product}", fatal=True)
        raise FeedError(f"Coinbase candles request failed: HTTP {e.code}")
    except (OSError, ValueError) as e:
        raise FeedError(f"Coinbase candles request failed: {e}")
    if not isinstance(rows, list):
        raise FeedError("unexpected reply from Coinbase")
    return rows


def _parse_time(s):
    """Coinbase ISO time such as 2026-10-10T18:30:01.123456Z -> epoch seconds."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _minute_key(epoch):
    return datetime.fromtimestamp(int(epoch // 60 * 60), timezone.utc).isoformat()


class CoinbaseProvider:
    """Real-time trades from Coinbase Exchange's public WebSocket, built into
    1-minute bars as they arrive. A background thread reads the socket;
    ``wait`` wakes the streamer the moment a trade lands. If the connection
    drops, the thread reconnects and refills the gap from 1-minute candles."""
    name = "Coinbase Exchange (real-time WebSocket)"
    simulated = False
    push = True
    WS_URL = "wss://ws-feed.exchange.coinbase.com"
    CANDLES_URL = "https://api.exchange.coinbase.com/products/{}/candles?granularity=60"
    MAX_BARS = 1440                                    # keep 24 hours of minutes

    def __init__(self, connect=_ws_connect, fetch_candles=_fetch_candles, clock=time.time,
                 retry_seconds=3.0):
        self._connect, self._fetch, self.clock, self.retry = connect, fetch_candles, clock, retry_seconds
        self._cond = threading.Condition()
        self._bars = {}                                # minute iso -> [o, h, l, c, v]
        self._dirty = False
        self._notes = []
        self._lat = collections.deque(maxlen=50)
        self._trades = 0
        self._last_trade = None                        # exchange time of newest trade
        self._stop = threading.Event()
        self._ws = None
        self._thread = None
        self._product = None

    # -- lifecycle
    def open(self, symbol):
        if self._thread is not None:
            return
        product = symbol.upper()
        if not is_crypto(product):
            raise FeedError(f"Coinbase lists crypto pairs such as BTC-USD, not {symbol}. "
                            f"Use Yahoo Finance for stocks.", fatal=True)
        self._product = product
        self._backfill()
        ws = self._open_ws()
        self._thread = threading.Thread(target=self._run, args=(ws,), daemon=True,
                                        name=f"coinbase-{product}")
        self._thread.start()

    def close(self):
        self._stop.set()
        with self._cond:
            ws = self._ws
            self._cond.notify_all()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    # -- streamer interface
    def bars(self, symbol=None):
        with self._cond:
            self._dirty = False
            if not self._bars:
                raise FeedError("waiting for the first Coinbase trade")
            return [(k, *self._bars[k]) for k in sorted(self._bars)]

    def wait(self, timeout):
        """Block until a new trade or a status note arrives (or timeout)."""
        with self._cond:
            if not self._dirty and not self._notes and not self._stop.is_set():
                self._cond.wait(timeout)
            return self._dirty

    def notes(self):
        with self._cond:
            out, self._notes = self._notes, []
            return out

    def latency(self):
        with self._cond:
            lat = sorted(self._lat)
            return {"feed_ms": lat[len(lat) // 2] if lat else None, "trades": self._trades,
                    "last_trade_ms": None if self._last_trade is None else self._last_trade * 1000}

    # -- internals
    def _note(self, level, text):
        with self._cond:
            self._notes.append((level, text))
            self._cond.notify_all()

    def _backfill(self):
        rows = self._fetch(self._product)
        with self._cond:
            for r in rows:
                try:
                    t, lo, hi, op, cl, vol = (float(x) for x in r[:6])
                except (TypeError, ValueError):
                    continue
                if min(lo, hi, op, cl) <= 0:
                    continue
                # exchange candles are the authority for minutes we may have missed
                self._bars[_minute_key(t)] = [op, max(hi, op, cl), min(lo, op, cl), cl, vol]
            self._trim()
            self._dirty = True
            self._cond.notify_all()

    def _open_ws(self):
        try:
            ws = self._connect(self.WS_URL)
            ws.send(json.dumps({"type": "subscribe", "product_ids": [self._product],
                                "channels": ["ticker", "heartbeat"]}))
        except FeedError:
            raise
        except Exception as e:
            raise FeedError(f"could not connect to Coinbase: {e}")
        with self._cond:
            self._ws = ws
        return ws

    def _trim(self):
        while len(self._bars) > self.MAX_BARS:
            del self._bars[min(self._bars)]

    def _run(self, ws):
        while not self._stop.is_set():
            try:
                self._read(ws)
                why = "connection closed"
            except Exception as e:
                why = str(e) or type(e).__name__
            try:
                ws.close()
            except Exception:
                pass
            if self._stop.is_set():
                return
            self._note("warn", f"Coinbase feed dropped ({why}). Reconnecting...")
            while not self._stop.wait(self.retry):
                try:
                    self._backfill()
                    ws = self._open_ws()
                except Exception:
                    continue
                self._note("ok", "Reconnected to Coinbase; missed minutes refilled from candles.")
                break

    def _read(self, ws):
        while not self._stop.is_set():
            raw = ws.recv()
            if not raw:
                return
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "ticker" and msg.get("product_id") == self._product:
                self._on_trade(msg, self.clock())
            elif kind == "error":
                raise FeedError(f"Coinbase: {msg.get('message', '')} {msg.get('reason', '')}".strip())

    def _on_trade(self, msg, now):
        try:
            price = float(msg["price"])
            size = float(msg.get("last_size") or 0.0)
        except (KeyError, TypeError, ValueError):
            return
        if not price > 0 or price != price:
            return
        ts = _parse_time(msg.get("time"))
        if ts is None:
            ts = now
        key = _minute_key(ts)
        with self._cond:
            b = self._bars.get(key)
            if b is None:
                self._bars[key] = [price, price, price, price, size]
                self._trim()
            else:
                b[1] = max(b[1], price)
                b[2] = min(b[2], price)
                b[3] = price
                b[4] += size
            self._trades += 1
            self._last_trade = ts
            self._lat.append((now - ts) * 1000.0)
            self._dirty = True
            self._cond.notify_all()


class UnavailableProvider:
    """Stands in for a source this server cannot offer; reports why and stops."""
    simulated, push = False, False

    def __init__(self, name, why):
        self.name, self.why = name, why

    def bars(self, symbol):
        raise FeedError(self.why, fatal=True)


def make_provider(source, symbol, public=False):
    """source: auto | coinbase | yahoo | sim. Auto picks Coinbase for crypto.
    On the public demo server, Yahoo is unavailable (cloud servers are often
    blocked by Yahoo, and its terms are for personal use)."""
    if source == "auto":
        source = "coinbase" if is_crypto(symbol) else "yahoo"
    if source == "coinbase":
        return CoinbaseProvider(), 15.0                # heartbeat if no trade in 15 s
    if source == "yahoo":
        if public:
            return UnavailableProvider("Yahoo Finance", "Stock prices from Yahoo are only available "
                                       "when you run RiskLens on your own computer. On this public "
                                       "demo, try a crypto pair (BTC-USD) or the simulated feed."), 20.0
        return YahooProvider(), 20.0
    if source == "sim":
        return SimProvider(), 2.0
    raise ValueError("source must be auto, coinbase, yahoo or sim")


# --------------------------------------------------------------- risk maths
def snapshot(symbol, bars, vol_target=0.15, dd_limit=0.15):
    """Live risk snapshot of the session so far."""
    closes = [b[4] for b in bars]
    highs = [b[2] for b in bars]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    per_year = MIN_PER_YEAR_CRYPTO if is_crypto(symbol) else MIN_PER_YEAR_STOCK
    w = rets[-VOL_WINDOW:]
    vol = None
    if len(w) >= 10:
        m = sum(w) / len(w)
        vol = math.sqrt(sum((r - m) ** 2 for r in w) / (len(w) - 1)) * math.sqrt(per_year)
    var = None
    vw = rets[-VAR_WINDOW:]
    if len(vw) >= 20:
        s = sorted(vw)
        k = max(1, math.ceil(round(0.05 * len(s), 9)))
        var = -s[k - 1]
    high = max(highs)
    dd = 1 - closes[-1] / high
    exposure = None if vol is None or vol <= 0 else min(1.0, vol_target / vol)
    breaker = "tripped" if dd >= dd_limit else "warning" if dd >= 0.7 * dd_limit else "armed"
    return {
        "symbol": symbol, "time": bars[-1][0], "price": closes[-1], "open": bars[0][1],
        "change": closes[-1] / bars[0][1] - 1, "high": high, "low": min(b[3] for b in bars),
        "drawdown": dd, "vol": vol, "var": var, "bars": len(bars),
        "exposure": exposure, "breaker": breaker, "vol_target": vol_target, "dd_limit": dd_limit,
        "crypto": is_crypto(symbol),
    }


def alerts_for(prev, cur):
    """Threshold crossings between two snapshots (each fires once per crossing)."""
    out = []
    if prev is None:
        return out
    if cur["breaker"] != prev["breaker"]:
        if cur["breaker"] == "tripped":
            out.append(["crit", "Drawdown breaker would fire",
                        f"{cur['drawdown']:.1%} below the session high (limit {cur['dd_limit']:.0%}). "
                        f"Rules say: go to cash."])
        elif cur["breaker"] == "warning":
            out.append(["warn", "Approaching the drawdown limit",
                        f"{cur['drawdown']:.1%} below the session high (limit {cur['dd_limit']:.0%})"])
        elif prev["breaker"] in ("warning", "tripped"):
            out.append(["ok", "Drawdown back within limits",
                        f"{cur['drawdown']:.1%} below the session high"])
    pv, cv = prev.get("vol"), cur.get("vol")
    if pv is not None and cv is not None:
        if cv > 2 * cur["vol_target"] >= pv:
            out.append(["warn", "Volatility spike",
                        f"60-minute volatility {cv:.0%}, over twice the {cur['vol_target']:.0%} target. "
                        f"Rules say: hold at most {cur['exposure']:.0%}."])
        elif pv > 2 * cur["vol_target"] >= cv:
            out.append(["ok", "Volatility calming", f"60-minute volatility back to {cv:.0%}"])
    return out


# ----------------------------------------------------------------- streamer
def _wire(bars):
    return [[b[0], b[4], b[2]] for b in bars]           # time, close, high


def stream(symbol, provider, emit, vol_target=0.15, dd_limit=0.15, poll_seconds=20.0,
           stale_after=300.0, sleep=time.sleep, clock=time.time, max_polls=None,
           min_interval=0.016, on_quote=None):
    """Backfill the session, then follow the feed until emit raises ClientGone.

    Polling providers are re-read every ``poll_seconds``. Push providers
    (Coinbase) wake the loop the moment a trade arrives; bursts are batched
    to at most one update per ``min_interval`` (16 ms, one 60 Hz screen
    frame) so the browser isn't flooded.

    ``on_quote(symbol, price, vol, max_age, source, simulated)`` is called on
    every new price; the pre-trade risk gateway uses it as its price feed.

    Events: "snap" (full session + snapshot), "tick" (changed bars + snapshot
    + alerts + latency), "status" (feed state messages)."""
    push = getattr(provider, "push", False)
    opener = getattr(provider, "open", None)
    latency = getattr(provider, "latency", lambda: {})
    notes = getattr(provider, "notes", lambda: [])
    max_age = 15.0 if push else max(10.0, poll_seconds * 6)

    def quote(snap):
        if on_quote is not None:
            on_quote(symbol, snap["price"], snap["vol"], max_age, provider.name, provider.simulated)
    try:
        bars = None
        while bars is None:
            try:
                if opener is not None:
                    opener(symbol)
                bars = provider.bars(symbol)
            except FeedError as e:
                emit("status", {"level": "crit", "text": str(e), "fatal": e.fatal})
                if e.fatal:
                    return
                sleep(max(poll_seconds, 5))
        t0 = time.perf_counter()
        snap = snapshot(symbol, bars, vol_target, dd_limit)
        compute_ms = (time.perf_counter() - t0) * 1000
        quote(snap)
        emit("snap", {"source": provider.name, "simulated": provider.simulated, "push": push,
                      "poll_seconds": poll_seconds, "bars": _wire(bars), "risk": snap,
                      "lat": latency() | {"compute_ms": compute_ms, "sent_ms": clock() * 1000}})
        last_change = clock()
        stale_sent = False
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            if push:
                provider.wait(poll_seconds)
            else:
                sleep(poll_seconds)
            for level, text in notes():
                emit("status", {"level": level, "text": text})
            try:
                new = provider.bars(symbol)
            except FeedError as e:
                emit("status", {"level": "warn", "text": f"{e}. Retrying in {poll_seconds:.0f} s."})
                continue
            known = {b[0]: b for b in bars}
            changed = [b for b in new if b[0] not in known or known[b[0]][4] != b[4]]
            if not changed:
                if not stale_sent and clock() - last_change >= stale_after:
                    stale_sent = True
                    emit("status", {"level": "info", "text":
                         "No new prices for 5 minutes. The market may be closed "
                         "(US stocks trade 9:30-16:00 ET on weekdays; crypto trades 24/7)."})
                else:
                    emit("status", {"level": "ok", "text": "heartbeat"})
                continue
            last_change = clock()
            stale_sent = False
            prev = snap
            bars = new
            t0 = time.perf_counter()
            snap = snapshot(symbol, bars, vol_target, dd_limit)
            alerts = alerts_for(prev, snap)
            compute_ms = (time.perf_counter() - t0) * 1000
            quote(snap)
            emit("tick", {"bars": _wire(changed), "risk": snap, "alerts": alerts,
                          "lat": latency() | {"compute_ms": compute_ms, "sent_ms": clock() * 1000}})
            if push and min_interval:
                sleep(min_interval)                    # batch bursts of trades
    finally:
        closer = getattr(provider, "close", None)
        if closer is not None:
            closer()
