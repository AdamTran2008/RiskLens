"""Replay and web app: the streamed replay must agree exactly with the
batch engine, resuming mid-way must not change anything, and the app's API
endpoints must work end to end over HTTP."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from risklens import engine, live, runner, strategies
from risklens.data import load_csv, register, synthetic, write_csv
from risklens.server import serve


@pytest.fixture
def data(tmp_path):
    write_csv(str(tmp_path / "S.csv"), synthetic(300, seed=4))
    return str(tmp_path / "S.csv")


CFG = {"symbol": "S", "strategy": {"name": "sma_cross", "params": {"fast": 5, "slow": 20}},
       "risk": {"vol_target": 0.15, "max_drawdown": 0.1, "cooldown_days": 5}}


def collect(cfg, path, **kw):
    out = []
    live.stream(runner.normalize(cfg), path, lambda k, p: out.append((k, p)),
                sleep=lambda s: None, **kw)
    return out


def test_stream_matches_batch_engine(data):
    ev = collect(CFG, data)
    assert ev[0][0] == "init" and ev[-1][0] == "end"
    days = [p for k, p in ev if k == "day"]
    assert len(days) == 300 and [d["t"] for d in days] == list(range(300))
    cfg = runner.normalize(CFG)
    bars = load_csv(data)
    batch = engine.run(bars, strategies.make("sma_cross", {"fast": 5, "slow": 20}),
                       10_000_000, engine.Costs(**cfg["costs"]), engine.RiskConfig(**cfg["risk"]))
    assert [d["eq"][1] for d in days] == [e / 100 for e in batch.equity]
    trips = sum(1 for d in days for k, _ in d["ev"] if k == "DRAWDOWN_BREAKER")
    assert trips == sum(1 for e in batch.risk_events if e[1] == "DRAWDOWN_BREAKER")


def test_resume_from_middle_is_identical(data):
    full = [p for k, p in collect(CFG, data) if k == "day"]
    part = [p for k, p in collect(CFG, data, start=137) if k == "day"]
    assert part == full[137:]


def test_client_disconnect_stops_stream(data):
    sent = []

    def emit(kind, payload):
        if len(sent) == 10:
            raise live.ClientGone()
        sent.append(kind)
    with pytest.raises(live.ClientGone):
        live.stream(runner.normalize(CFG), data, emit, sleep=lambda s: None)
    assert len(sent) == 10


@pytest.fixture
def srv(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write_csv(str(d / "S.csv"), synthetic(400, seed=4))
    register(str(d), "S", "S.csv", source="test")
    app, httpd = serve("127.0.0.1", 0, str(d), str(tmp_path / "res"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.headers.get("Content-Type"), r.read().decode()


def test_app_page_and_apis(srv):
    app, base = srv
    ctype, html = get(base + "/")
    assert ctype.startswith("text/html") and "Live market" in html and "Replay" in html and app.version in html
    cards = json.loads(get(base + "/api/overview")[1])
    assert cards[0]["symbol"] == "S" and cards[0]["status"] in ("calm", "elevated", "stressed")
    # overview snapshots are internal, so history starts empty
    assert json.loads(get(base + "/api/history")[1]) == []
    req = urllib.request.Request(base + "/runs", data=json.dumps(CFG | {"symbol": "S"}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    rid = json.loads(urllib.request.urlopen(req).read())["run_id"]
    hist = json.loads(get(base + "/api/history")[1])
    assert [h["run_id"] for h in hist] == [rid]
    assert 'class="app embed"' in get(base + f"/report?runs={rid}&embed=1")[1]


def test_live_endpoint_streams_events(srv):
    _, base = srv
    q = urllib.parse.quote(json.dumps(CFG | {"symbol": "S"}))
    ctype, body = get(base + f"/api/live?cfg={q}&speed=2000&from=390")
    assert ctype == "text/event-stream"
    kinds = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
    assert kinds[0] == "init" and kinds[-1] == "end" and kinds.count("day") == 10


def test_live_endpoint_rejects_bad_config(srv):
    _, base = srv
    q = urllib.parse.quote(json.dumps({"symbol": "S", "evil": 1}))
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + f"/api/live?cfg={q}")
    assert e.value.code == 400
