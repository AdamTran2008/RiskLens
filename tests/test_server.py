"""End to end over real HTTP: server + clients, idempotent content-addressed
runs, strict validation, concurrency, and tamper detection."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from risklens import runner
from risklens.client import Client
from risklens.data import register, synthetic, write_csv
from risklens.server import serve


@pytest.fixture
def srv(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_csv(str(data / "SYN.csv"), synthetic(400, seed=3))
    register(str(data), "SYN", "SYN.csv", source="test")
    app, httpd = serve("127.0.0.1", 0, str(data), str(tmp_path / "results"))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield app, base, tmp_path
    httpd.shutdown()
    httpd.server_close()


CFG = {"symbol": "SYN", "strategy": {"name": "sma_cross", "params": {"fast": 10, "slow": 30}},
       "risk": {"vol_target": 0.15, "max_drawdown": 0.1}, "luck_test": {"trials": 50}}


def post(base, body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(base + "/runs", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_run_then_cached(srv):
    app, base, _ = srv
    s1, r1 = post(base, CFG)
    s2, r2 = post(base, CFG)
    assert s1 == s2 == 200
    assert (r1["cached"], r2["cached"]) == (False, True)
    assert r1["run_id"] == r2["run_id"]
    assert r1["manifest"]["result_fingerprint"] == r2["manifest"]["result_fingerprint"]
    full = Client(base).get(r1["run_id"])
    assert len(full["series"]["equity"]) == 400
    html = Client(base).report([r1["run_id"]]).decode()
    assert "<svg" in html and r1["run_id"] in html


def test_different_config_different_id(srv):
    _, base, _ = srv
    a = post(base, CFG)[1]["run_id"]
    b = post(base, {**CFG, "costs": {"fee_bps": 5}})[1]["run_id"]
    assert a != b


def test_defaults_are_part_of_identity(srv):
    """Spelling out a default yields the same run as omitting it."""
    _, base, _ = srv
    a = post(base, CFG)[1]["run_id"]
    b = post(base, {**CFG, "costs": {"fee_bps": 1.0, "slippage_bps": 2.0}})[1]["run_id"]
    assert a == b


@pytest.mark.parametrize("bad", [
    {**CFG, "bogus": 1},
    {**CFG, "symbol": "NOPE"},
    {**CFG, "symbol": "../etc"},
    {**CFG, "risk": {"max_exposure": 2.0}},            # leverage not allowed
    {**CFG, "risk": {"max_exposure": True}},           # bool is not a number
    {**CFG, "risk": {"vol_target": "0.1"}},
    {**CFG, "strategy": {"name": "sma_cross", "params": {"fast": 50, "slow": 10}}},
    {**CFG, "strategy": {"name": "magic"}},
    {**CFG, "start": "2030-01-01"},                    # no data in range
    {**CFG, "start": "2020-13-45x"},
    [1, 2, 3],
])
def test_rejects_bad_configs(srv, bad):
    status, body = post(srv[1], bad)
    assert status == 400 and "error" in body


def test_rejects_garbage_and_nan(srv):
    assert post(srv[1], b"{not json", raw=True)[0] == 400
    assert post(srv[1], b'{"symbol":"SYN","risk":{"vol_target":NaN}}', raw=True)[0] == 400


def test_oversized_body(srv):
    assert post(srv[1], b"x" * (65 * 1024), raw=True)[0] == 413


def test_path_traversal_blocked(srv):
    for rid in ["../../etc/passwd", "..%2F..%2Fx", "a" * 24 + "/../x"]:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(srv[1] + "/runs/" + rid)
        assert e.value.code == 404


def test_concurrent_identical_requests_compute_once(srv):
    app, base, _ = srv
    cfg = {**CFG, "luck_test": {"trials": 300}}
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(base, cfg)[1])) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({r["run_id"] for r in out}) == 1
    assert app.stats["runs_computed"] == 1 and app.stats["cache_hits"] == 7


def test_server_refuses_tampered_data(srv):
    _, _, tmp = srv
    with open(tmp / "data" / "SYN.csv", "a") as f:
        f.write("2099-01-01,1,1,1,1,1\n")
    with pytest.raises(SystemExit, match="does not match"):
        serve("127.0.0.1", 0, str(tmp / "data"), str(tmp / "results2"))


def test_execute_is_deterministic(tmp_path):
    write_csv(str(tmp_path / "S.csv"), synthetic(300, seed=9))
    cfg = runner.normalize({"symbol": "S", "luck_test": {"trials": 20}})
    a = runner.execute(cfg, str(tmp_path / "S.csv"))
    b = runner.execute(cfg, str(tmp_path / "S.csv"))
    assert a["run_id"] == b["run_id"]
    assert a["manifest"] == b["manifest"] and a["summary"] == b["summary"]
    assert a["luck_test"] == b["luck_test"]


def test_run_id_covers_config_data_and_engine():
    cfg = runner.normalize({"symbol": "S"})
    base = runner.run_id(cfg, "a" * 64, "v1")
    assert runner.run_id(cfg, "b" * 64, "v1") != base           # different data
    assert runner.run_id(cfg, "a" * 64, "v2") != base           # different engine code
    assert runner.run_id({**cfg, "initial_cash": 5000.0}, "a" * 64, "v1") != base
