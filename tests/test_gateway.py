"""Pre-trade risk gateway: every check, the kill switch, and the core
invariant under random order sequences: an approved order never leaves the
account breaking a limit, and the books always reconcile exactly."""
import json
import threading
import urllib.error
import urllib.request
from fractions import Fraction as Q

import pytest
from hypothesis import given, settings, strategies as st

from risklens import gateway
from risklens.data import register, synthetic, write_csv
from risklens.server import serve

SID = "test-session-1"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def gw():
    c = Clock()
    g = gateway.Gateway(clock=c)
    g.clock_obj = c
    return g


def order(g, side, qty, sym="BTC-USD", sid=SID):
    return g.submit(sid, {"symbol": sym, "side": side, "qty": qty})


def failed(d):
    return [c["name"] for c in d["checks"] if not c["ok"]]


def test_happy_path_fills_and_reconciles(gw):
    gw.mark("BTC-USD", 50_000.0, vol=0.10)
    d = order(gw, "buy", "0.4")
    assert d["approved"] and len(d["checks"]) == 8 and d["reason"] == "All checks passed"
    st_ = gw.state(SID)
    notional = Q("0.4") * Q(50_000) * (1 + Q(2, 10_000))
    fee = notional / 10_000
    assert st_["cash"] == round(float(100_000 - notional - fee), 2)
    assert st_["positions"][0]["qty"] == "0.4" and st_["reconciles"]
    assert order(gw, "sell", "0.4")["approved"]
    assert gw.state(SID)["positions"] == [] and gw.state(SID)["reconciles"]


def test_fails_closed_without_a_fresh_price(gw):
    d = order(gw, "buy", "0.1")
    assert not d["approved"] and failed(d) == ["Live price"] and "Connect" in d["reason"]
    gw.mark("BTC-USD", 50_000.0, max_age=10)
    gw.clock_obj.t += 11
    d = order(gw, "buy", "0.1")
    assert not d["approved"] and "old" in d["reason"]


@pytest.mark.parametrize("bad", [{"side": "buy", "qty": "0"}, {"side": "buy", "qty": "-1"},
                                 {"side": "hold", "qty": "1"}, {"side": "buy", "qty": "0.000000001"},
                                 {"side": "buy", "qty": "abc"}, {"side": "buy", "qty": True},
                                 {"side": "buy", "qty": "1", "price": 5}])
def test_invalid_orders_rejected(gw, bad):
    gw.mark("BTC-USD", 100.0)
    d = gw.submit(SID, {"symbol": "BTC-USD"} | bad)
    assert not d["approved"] and failed(d) == ["Valid order"]


def test_fat_finger_position_and_cash_limits(gw):
    gw.mark("BTC-USD", 10_000.0)
    assert failed(order(gw, "buy", "3")) == ["Order size"]                 # $30k > $25k
    gw.set_limits(SID, {"max_order_value": 1e6, "max_position_value": 30_000})
    assert failed(order(gw, "buy", "3.5")) == ["Position limit"]           # $35k > $30k
    gw.set_limits(SID, {"max_position_value": 1e6, "max_exposure": 1.0, "vol_target": None})
    d = order(gw, "buy", "11")                                              # $110k > cash
    assert "Enough cash" in failed(d)


def test_exposure_follows_volatility_target(gw):
    gw.set_limits(SID, {"max_order_value": 1e6, "max_position_value": 1e6})
    gw.mark("BTC-USD", 1_000.0, vol=0.60)                # allowed = 15% / 60% = 25%
    assert failed(order(gw, "buy", "30")) == ["Exposure limit"]            # 30% of equity
    d = order(gw, "buy", "24")
    assert d["approved"]
    assert "vol target" in [c for c in d["checks"] if c["name"] == "Exposure limit"][0]["detail"]


def test_no_short_selling(gw):
    gw.mark("BTC-USD", 100.0)
    assert failed(order(gw, "sell", "1")) == ["No short selling"]


def test_rate_limit(gw):
    gw.mark("BTC-USD", 100.0)
    gw.set_limits(SID, {"max_orders_per_minute": 3})
    assert [order(gw, "buy", "1")["approved"] for _ in range(4)] == [True, True, True, False]
    gw.clock_obj.t += 61
    gw.mark("BTC-USD", 100.0)                       # keep the price fresh
    assert order(gw, "buy", "1")["approved"]


def test_manual_kill_switch_flattens_and_blocks_buys(gw):
    gw.mark("BTC-USD", 10_000.0)
    order(gw, "buy", "1")
    s = gw.kill(SID)
    assert s["kill"]["by"] == "manual" and s["positions"] == [] and s["reconciles"]
    assert s["log"][0].get("flatten")
    assert failed(order(gw, "buy", "0.1")) == ["Kill switch"]
    gw.rearm(SID)
    assert order(gw, "buy", "0.1")["approved"]


def test_auto_kill_on_drawdown(gw):
    gw.set_limits(SID, {"kill_drawdown": 0.10, "max_position_value": 1e6, "max_order_value": 1e6,
                        "vol_target": None})
    gw.mark("BTC-USD", 1_000.0)
    order(gw, "buy", "90")                          # 90% invested
    gw.mark("BTC-USD", 950.0)                       # account down ~4.5%: fine
    assert gw.state(SID)["kill"] is None
    gw.mark("BTC-USD", 880.0)                       # account down ~10.8%: trips
    s = gw.state(SID)
    assert s["kill"]["by"] == "auto" and s["positions"] == [] and s["reconciles"]
    assert s["drawdown"] >= 0.10
    gw.mark("BTC-USD", 2_000.0)                     # rally after the stop: no effect on cash
    assert gw.state(SID)["equity"] == s["equity"]


def test_sessions_are_separate_and_bounded(gw):
    gw.mark("BTC-USD", 100.0)
    order(gw, "buy", "1", sid="aaaaaaaa1")
    assert gw.state("bbbbbbbb2")["positions"] == []
    with pytest.raises(gateway.GatewayError):
        gw.state("bad id!")
    for i in range(gateway.MAX_SESSIONS + 5):
        gw.state(f"session-{i:05d}")
    assert len(gw.accounts) == gateway.MAX_SESSIONS


def test_limits_validation():
    with pytest.raises(gateway.GatewayError):
        gateway.limits_from({"max_exposure": 2})
    with pytest.raises(gateway.GatewayError):
        gateway.limits_from({"evil": 1})
    with pytest.raises(gateway.GatewayError):
        gateway.limits_from({"max_order_value": float("nan")})
    assert gateway.limits_from({"vol_target": None}).vol_target is None


# --------------------------------------------------------------- invariant
OPS = st.lists(st.one_of(
    st.tuples(st.just("price"), st.sampled_from(["BTC-USD", "ETH-USD"]),
              st.floats(0.7, 1.4), st.one_of(st.none(), st.floats(0.05, 1.5))),
    st.tuples(st.just("order"), st.sampled_from(["BTC-USD", "ETH-USD", "SOL-USD"]),
              st.sampled_from(["buy", "sell"]), st.decimals("0.00000001", "40", places=8)),
    st.tuples(st.just("kill"), st.booleans()),
    st.tuples(st.just("rearm")),
    st.tuples(st.just("wait"), st.floats(0, 30)),
    st.tuples(st.just("limits"), st.floats(1_000, 80_000), st.floats(0.1, 1.0),
              st.one_of(st.none(), st.floats(0.03, 0.5))),
), min_size=1, max_size=60)


@settings(max_examples=250, deadline=None)
@given(OPS)
def test_no_approved_order_ever_breaks_a_limit(ops):
    clock = Clock()
    g = gateway.Gateway(clock=clock)
    px = {"BTC-USD": 1_000.0, "ETH-USD": 100.0}
    for s, p in px.items():
        g.mark(s, p, vol=0.2)
    for op in ops:
        acct = g.account(SID)
        kill_before = acct.kill is not None
        if op[0] == "price":
            px[op[1]] = round(px[op[1]] * op[2], 2)
            g.mark(op[1], px[op[1]], vol=op[3])
        elif op[0] == "order":
            d = g.submit(SID, {"symbol": op[1], "side": op[2], "qty": str(op[3])})
            if d["approved"]:
                assert all(c["ok"] for c in d["checks"])
                q = g.quotes[op[1]]
                assert clock() - q["time"] <= q["max_age"]                 # fresh price only
                if op[2] == "buy":
                    assert not kill_before                                  # kill switch holds
                    lim = acct.limits
                    held = acct.pos[op[1]] * q["price"]
                    assert held <= Q(str(lim.max_position_value))           # position limit
                    eq = g._equity(acct)
                    allowed, _ = g.allowed_exposure(acct, q["vol"])
                    assert g._gross(acct) <= Q(allowed) * eq                # exposure limit
                    assert Q(str(d["value"])) <= Q(str(lim.max_order_value)) + Q(1, 100)
        elif op[0] == "kill":
            g.kill(SID, flatten=op[1])
        elif op[0] == "rearm":
            g.rearm(SID)
        elif op[0] == "wait":
            clock.t += op[1]
        else:
            g.set_limits(SID, {"max_position_value": op[1], "max_exposure": op[2],
                               "vol_target": op[3]})
        assert acct.cash >= 0                                               # never overdrawn
        assert all(q > 0 for q in acct.pos.values())                        # never short
        assert acct.reconciles()                                            # books balance exactly
        s = g.state(SID)
        if s["kill"] and s["kill"]["by"] == "auto":
            assert s["positions"] == [] or not kill_before


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def srv(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write_csv(str(d / "S.csv"), synthetic(300, seed=4))
    register(str(d), "S", "S.csv", source="test")
    app, httpd = serve("127.0.0.1", 0, str(d), str(tmp_path / "res"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(base, path, body=None, sid=SID):
    req = urllib.request.Request(base + path, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-RiskLens-Session": sid, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_gateway_endpoints(srv):
    app, base = srv
    app.gateway.mark("BTC-USD", 20_000.0, vol=0.3)
    r = call(base, "/api/gateway/order", {"symbol": "BTC-USD", "side": "buy", "qty": 0.5})
    assert r["decision"]["approved"] and r["state"]["positions"][0]["symbol"] == "BTC-USD"
    assert r["decision"]["check_us"] > 0
    assert call(base, "/api/gateway/kill", {})["kill"]["by"] == "manual"
    assert call(base, "/api/gateway/rearm", {})["kill"] is None
    assert call(base, "/api/gateway/limits", {"max_order_value": 500})["limits"]["max_order_value"] == 500
    assert call(base, "/api/gateway/reset", {})["log"] == []
    assert call(base, "/api/gateway")["cash"] == 100_000
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/gateway/limits", {"max_exposure": 5})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/gateway", sid="no")
    assert e.value.code == 400


def test_market_stream_feeds_the_gateway(srv):
    app, base = srv
    with urllib.request.urlopen(base + "/api/market?symbol=SIMX&source=sim", timeout=5) as r:
        r.readline()
        r.readline()
    assert app.gateway.quotes["SIMX"]["simulated"] is True
    d = call(base, "/api/gateway/order", {"symbol": "SIMX", "side": "buy", "qty": 1})
    assert [c["name"] for c in d["decision"]["checks"]][:2] == ["Valid order", "Live price"]
    assert d["decision"]["checks"][1]["ok"]


def test_exposure_limit_counts_trading_costs(gw):
    """At the boundary, fees and slippage are what tip an order over the limit."""
    gw.set_limits(SID, {"max_exposure": 0.5, "vol_target": None, "max_order_value": 1e6,
                        "max_position_value": 1e6})
    gw.mark("BTC-USD", 100.0)
    assert failed(order(gw, "buy", "500")) == ["Exposure limit"]   # costs push it to 50.0075%
    gw.set_limits(SID, {"fee_bps": 0, "slippage_bps": 0})
    assert order(gw, "buy", "500")["approved"]                     # exactly 50%: allowed
