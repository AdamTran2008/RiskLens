"""Live market mode: risk snapshot maths, alert crossings, the streamer's
polling and push loops, the Coinbase WebSocket provider (against a fake
socket that speaks Coinbase's message format), and the /api/market endpoint."""
import json
import queue
import threading
import time
import urllib.error
import urllib.request

import pytest

from risklens import market
from risklens.data import register, synthetic, write_csv
from risklens.server import serve


def bars_from(closes, start=0):
    """Minute bars with high = max(open, close), low = min(open, close)."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append((f"2026-10-10T{(start + i) // 60:02d}:{(start + i) % 60:02d}:00+00:00",
                    prev, max(prev, c), min(prev, c), c, 1.0))
        prev = c
    return out


# ------------------------------------------------------------------ snapshot
def test_snapshot_basic_numbers():
    s = market.snapshot("SPY", bars_from([100, 110, 99]), vol_target=0.15, dd_limit=0.12)
    assert s["price"] == 99 and s["high"] == 110 and s["low"] == 99
    assert s["drawdown"] == pytest.approx(1 - 99 / 110)
    assert s["change"] == pytest.approx(99 / 100 - 1)
    assert s["vol"] is None and s["var"] is None and s["exposure"] is None   # not enough data yet
    assert s["breaker"] == "warning"                                        # 10% >= 0.7 x 12%


def test_snapshot_vol_var_and_exposure_cap():
    closes = [100 * (1.001 if i % 2 else 0.999) ** (i % 7) for i in range(300)]
    s = market.snapshot("BTC-USD", bars_from(closes), vol_target=0.15)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, 300)]
    w = rets[-60:]
    m = sum(w) / 60
    want = (sum((r - m) ** 2 for r in w) / 59) ** 0.5 * (365 * 24 * 60) ** 0.5
    assert s["vol"] == pytest.approx(want)
    worst = sorted(rets[-240:])
    assert s["var"] == pytest.approx(-worst[11])          # nearest rank: ceil(0.05 x 240) = 12th
    assert s["exposure"] == pytest.approx(min(1, 0.15 / want))
    calm = market.snapshot("BTC-USD", bars_from([100 + 1e-6 * (i % 2) for i in range(80)]))
    assert calm["exposure"] == 1.0                        # never above 100%


def test_stock_vs_crypto_annualization():
    closes = [100 + (i % 3) for i in range(80)]
    a = market.snapshot("SPY", bars_from(closes))["vol"]
    b = market.snapshot("ETH-USD", bars_from(closes))["vol"]
    assert b / a == pytest.approx(((365 * 24 * 60) / (252 * 390)) ** 0.5)


def test_breaker_states():
    assert market.snapshot("X", bars_from([100, 100]))["breaker"] == "armed"
    assert market.snapshot("X", bars_from([100, 89]), dd_limit=0.15)["breaker"] == "warning"
    assert market.snapshot("X", bars_from([100, 85]), dd_limit=0.15)["breaker"] == "tripped"


def test_alerts_fire_once_per_crossing():
    a = market.snapshot("X", bars_from([100, 100]))
    b = market.snapshot("X", bars_from([100, 84]))
    assert market.alerts_for(None, a) == []
    [[level, title, _]] = market.alerts_for(a, b)
    assert level == "crit" and "breaker" in title
    assert market.alerts_for(b, b) == []                   # no repeat while still tripped
    [[level, _, _]] = market.alerts_for(b, a)
    assert level == "ok"


# ------------------------------------------------------------------ streamer
class ListProvider:
    """Polling provider that returns a scripted sequence of bar lists."""
    name, simulated, push = "test", False, False

    def __init__(self, script):
        self.script = list(script)

    def bars(self, symbol):
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def run_stream(provider, **kw):
    out = []
    t = [0.0]

    def sleep(s):
        t[0] += s
    market.stream("X", provider, lambda k, p: out.append((k, p)), sleep=sleep,
                  clock=lambda: t[0], **kw)
    return out


def test_poll_stream_snap_tick_heartbeat_and_stale():
    b1 = bars_from([100, 101])
    b2 = bars_from([100, 101, 102])
    ev = run_stream(ListProvider([b1, b2, b2, b2]), poll_seconds=20, stale_after=60, max_polls=6)
    kinds = [k for k, _ in ev]
    assert kinds[0] == "snap" and len(ev[0][1]["bars"]) == 2
    assert kinds[1] == "tick" and ev[1][1]["bars"] == [[b2[2][0], 102, 102]]
    texts = [p["text"] for k, p in ev if k == "status"]
    assert texts.count("heartbeat") == 4 and sum("market may be closed" in t for t in texts) == 1
    assert ev[1][1]["lat"]["compute_ms"] >= 0 and "sent_ms" in ev[1][1]["lat"]


def test_poll_stream_survives_feed_errors():
    b1 = bars_from([100, 101])
    ev = run_stream(ListProvider([market.FeedError("boom"), b1, market.FeedError("flaky"),
                                  bars_from([100, 101, 99])]), max_polls=2)
    kinds = [k for k, _ in ev]
    assert kinds == ["status", "snap", "status", "tick"]


def test_fatal_error_stops_stream():
    ev = run_stream(ListProvider([market.FeedError("bad symbol", fatal=True)]))
    assert ev == [("status", {"level": "crit", "text": "bad symbol", "fatal": True})]


def test_stream_stops_when_client_leaves():
    sent = []

    def emit(kind, payload):
        if len(sent) == 3:
            raise market.ClientGone()
        sent.append(kind)
    with pytest.raises(market.ClientGone):
        market.stream("X", market.SimProvider(), emit, poll_seconds=0, sleep=lambda s: None)
    assert len(sent) == 3


def test_sim_provider_is_deterministic_and_advances():
    t = [0.0]
    p = market.SimProvider(seconds_per_bar=2, history=50, clock=lambda: t[0])
    a = p.bars("SIM")
    t[0] = 10.0
    b = p.bars("SIM")
    assert len(a) == 50 and len(b) == 55 and b[:50] == a
    assert all(lo <= min(o, c) <= max(o, c) <= hi for _, o, hi, lo, c, _ in b)


def test_make_provider_routing():
    assert isinstance(market.make_provider("auto", "BTC-USD")[0], market.CoinbaseProvider)
    assert isinstance(market.make_provider("auto", "SPY")[0], market.YahooProvider)
    assert isinstance(market.make_provider("sim", "SPY")[0], market.SimProvider)
    with pytest.raises(ValueError):
        market.make_provider("tradingview", "SPY")


# ----------------------------------------------------------- Coinbase (fake)
class FakeWS:
    """Speaks Coinbase Exchange's WebSocket message format from a queue."""

    def __init__(self):
        self.q, self.sent, self.closed = queue.Queue(), [], threading.Event()

    def send(self, s):
        self.sent.append(json.loads(s))

    def recv(self):
        while True:
            if self.closed.is_set():
                raise ConnectionError("socket closed")
            try:
                return self.q.get(timeout=0.02)
            except queue.Empty:
                pass

    def close(self):
        self.closed.set()

    def trade(self, price, t, size="0.01", product="BTC-USD"):
        self.q.put(json.dumps({"type": "ticker", "product_id": product, "price": str(price),
                               "last_size": size, "time": t, "sequence": 1}))


T0 = 1791660000                               # a whole minute, epoch seconds


def candles(n=30, start_px=60000.0):
    rows, px = [], start_px
    for i in range(n):                         # Coinbase order: newest first
        t = T0 - (n - i) * 60
        rows.append([t, px - 5, px + 5, px, px + 1, 2.5])
        px += 1
    return rows[::-1]


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + f".{int(epoch % 1 * 1e6):06d}Z"


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_coinbase_backfill_subscribe_and_build_bars():
    ws = FakeWS()
    now = [T0 + 30.25]
    p = market.CoinbaseProvider(connect=lambda url: ws, fetch_candles=lambda prod: candles(),
                                clock=lambda: now[0])
    p.open("btc-usd")
    try:
        assert ws.sent == [{"type": "subscribe", "product_ids": ["BTC-USD"],
                            "channels": ["ticker", "heartbeat"]}]
        bars = p.bars()
        assert len(bars) == 30 and bars[0][0] < bars[-1][0]          # sorted oldest first
        ws.trade(61000.0, iso(T0 + 30.2))
        ws.trade(61010.0, iso(T0 + 30.21), size="0.5")
        ws.q.put(json.dumps({"type": "ticker", "product_id": "ETH-USD", "price": "1",
                             "time": iso(T0 + 30.22)}))                # other product: ignored
        ws.q.put(json.dumps({"type": "heartbeat", "product_id": "BTC-USD"}))
        ws.trade(60990.0, iso(T0 + 30.23))
        assert wait_for(lambda: p.latency()["trades"] == 3)
        assert p.wait(0.01) is True
        bars = p.bars()
        assert len(bars) == 31
        key, o, h, l, c, v = bars[-1]
        assert key == market._minute_key(T0)
        assert (o, h, l, c) == (61000.0, 61010.0, 60990.0, 60990.0)
        assert v == pytest.approx(0.52)
        lat = p.latency()
        assert lat["feed_ms"] == pytest.approx(40.0, abs=1)          # 30.25 - 30.21 s, median
        assert lat["last_trade_ms"] == pytest.approx((T0 + 30.23) * 1000)
        assert p.wait(0.01) is False                                  # nothing new since bars()
    finally:
        p.close()
    assert ws.closed.is_set()


def test_coinbase_reconnects_and_refills_gap():
    sockets = [FakeWS(), FakeWS()]
    made = []

    def connect(url):
        made.append(sockets[len(made)])
        return made[-1]
    p = market.CoinbaseProvider(connect=connect, fetch_candles=lambda prod: candles(),
                                retry_seconds=0.01)
    p.open("BTC-USD")
    try:
        sockets[0].q.put(json.dumps({"type": "error", "message": "boom"}))
        assert wait_for(lambda: len(made) == 2)
        assert wait_for(lambda: any("Reconnected" in t for _, t in p.notes()))
        sockets[1].trade(62000.0, iso(T0 + 61))
        assert wait_for(lambda: p.latency()["trades"] == 1)
    finally:
        p.close()


def test_coinbase_rejects_stocks_without_connecting():
    p = market.CoinbaseProvider(connect=lambda url: pytest.fail("should not connect"),
                                fetch_candles=lambda prod: pytest.fail("should not fetch"))
    with pytest.raises(market.FeedError) as e:
        p.open("AAPL")
    assert e.value.fatal


def test_push_stream_end_to_end_with_fake_coinbase():
    ws = FakeWS()
    p = market.CoinbaseProvider(connect=lambda url: ws, fetch_candles=lambda prod: candles())
    out = []
    ready = threading.Event()

    def emit(kind, payload):
        out.append((kind, payload))
        if kind == "snap":
            ready.set()
        if sum(k == "tick" for k, _ in out) == 2:
            raise market.ClientGone()
    th = threading.Thread(target=lambda: pytest.raises(market.ClientGone, market.stream, "BTC-USD",
                                                       p, emit, poll_seconds=5, min_interval=0))
    th.start()
    assert ready.wait(2)
    ws.trade(61000.0, iso(time.time()))
    assert wait_for(lambda: sum(k == "tick" for k, _ in out) == 1)
    ws.trade(59000.0, iso(time.time()))
    th.join(3)
    assert not th.is_alive() and ws.closed.is_set()          # socket closed when client left
    snap = out[0][1]
    assert snap["push"] is True and len(snap["bars"]) == 30
    tick = [pl for k, pl in out if k == "tick"][-1]
    assert tick["risk"]["price"] == 59000.0 and tick["lat"]["trades"] == 2
    assert tick["lat"]["feed_ms"] is not None and tick["lat"]["compute_ms"] < 50


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def srv(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write_csv(str(d / "S.csv"), synthetic(300, seed=4))
    register(str(d), "S", "S.csv", source="test")
    app, httpd = serve("127.0.0.1", 0, str(d), str(tmp_path / "res"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_market_endpoint_streams_sim_feed(srv):
    with urllib.request.urlopen(srv + "/api/market?symbol=SIM&source=sim&vol=0.2&dd=0.1",
                                timeout=5) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        assert r.readline().decode().strip() == "event: snap"
        snap = json.loads(r.readline().decode()[6:])
    assert snap["simulated"] is True and len(snap["bars"]) == 180
    assert snap["risk"]["vol_target"] == 0.2 and snap["risk"]["dd_limit"] == 0.1


@pytest.mark.parametrize("q", ["symbol=BAD%20SYM", "source=tradingview", "vol=5", "dd=0"])
def test_market_endpoint_rejects_bad_params(srv, q):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(srv + "/api/market?" + q, timeout=5)
    assert e.value.code == 400
