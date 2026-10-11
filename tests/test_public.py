"""Public demo mode: the limits that keep a shared server usable."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from risklens import market, server
from risklens.data import register, synthetic, write_csv


@pytest.fixture
def pub(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write_csv(str(d / "S.csv"), synthetic(300, seed=4))
    register(str(d), "S", "S.csv", source="test")
    app, httpd = server.serve("127.0.0.1", 0, str(d), str(tmp_path / "res"), public=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def post(base, path, body, ip="1.2.3.4"):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Forwarded-For": ip})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def code(fn):
    try:
        fn()
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def test_health_reports_public(pub):
    _, base = pub
    assert json.loads(urllib.request.urlopen(base + "/health").read())["public"] is True


def test_luck_test_size_capped(pub):
    _, base = pub
    big = {"symbol": "S", "luck_test": {"trials": 5000}}
    assert code(lambda: post(base, "/runs", big)) == 400
    assert code(lambda: post(base, "/runs", {"symbol": "S", "luck_test": {"trials": 0}})) == 200


def test_runs_rate_limited_per_client(pub):
    _, base = pub
    cfg = {"symbol": "S", "luck_test": {"trials": 0}}
    codes = [code(lambda: post(base, "/runs", cfg, ip="9.9.9.9")) for _ in range(server.PUBLIC["runs_per_minute"] + 1)]
    assert codes[:-1] == [200] * server.PUBLIC["runs_per_minute"] and codes[-1] == 429
    assert code(lambda: post(base, "/runs", cfg, ip="8.8.8.8")) == 200     # other visitors unaffected


def test_stream_slots_capped_and_released():
    slots = server.StreamSlots(total=3, per_client=2)
    assert slots.acquire("a") and slots.acquire("a") and not slots.acquire("a")
    assert slots.acquire("b") and not slots.acquire("c")                   # total cap
    slots.release("a")
    assert slots.acquire("c")


def test_rate_limiter_window():
    t = [0.0]
    rl = server.RateLimiter(2, clock=lambda: t[0])
    assert rl.allow("k") and rl.allow("k") and not rl.allow("k")
    t[0] = 60.0
    assert rl.allow("k")


def test_yahoo_unavailable_on_public_server():
    p, _ = market.make_provider("auto", "SPY", public=True)
    out = []
    market.stream("SPY", p, lambda k, d: out.append((k, d)), sleep=lambda s: None)
    assert out[0][0] == "status" and out[0][1]["fatal"] and "your own computer" in out[0][1]["text"]


def test_store_is_pruned(pub, monkeypatch):
    app, base = pub
    monkeypatch.setitem(server.PUBLIC, "max_stored_results", 3)
    monkeypatch.setitem(server.PUBLIC, "runs_per_minute", 100)
    app.run_limit.per = 100
    for fast in range(2, 9):
        post(base, "/runs", {"symbol": "S", "luck_test": {"trials": 0},
                             "strategy": {"name": "sma_cross", "params": {"fast": fast, "slow": 30}}})
    import os
    assert len([f for f in os.listdir(app.results_dir) if f.endswith(".json")]) <= 3
