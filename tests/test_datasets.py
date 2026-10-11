"""Adding stocks from the app: downloaded, frozen, hashed and usable at once,
without a restart, and a failed download never leaves anything behind."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from risklens import fetch, runner
from risklens.data import load_manifest, register, sha256_file, synthetic, write_csv
from risklens import server as server_mod
from risklens.server import App, serve


def fake_fetcher(calls, n=400, seed=9):
    def f(symbol, start, end):
        calls.append((symbol, start, end))
        return synthetic(n, seed=seed)
    return f


@pytest.fixture
def srv(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write_csv(str(d / "S.csv"), synthetic(300, seed=4))
    register(str(d), "S", "S.csv", source="test")
    app, httpd = serve("127.0.0.1", 0, str(d), str(tmp_path / "res"))
    app.calls = []
    app.fetcher = fake_fetcher(app.calls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()
    httpd.server_close()


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def status(fn):
    try:
        fn()
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def test_add_stock_then_analyze_immediately(srv):
    app, base, tmp = srv
    e = post(base, "/api/datasets", {"symbol": "tsla", "start": "2015-01-01"})
    assert e["symbol"] == "TSLA" and e["rows"] == 400
    assert app.calls == [("TSLA", "2015-01-01", None)]
    listed = json.loads(urllib.request.urlopen(base + "/datasets").read())
    assert set(listed) == {"S", "TSLA"}
    r = post(base, "/runs", {"symbol": "TSLA", "luck_test": {"trials": 0}})
    assert r["summary"]["days"] == 400
    # frozen and fingerprinted: the manifest hash matches the file, and a
    # freshly started server accepts it
    m = load_manifest(str(tmp / "data"))
    assert m["TSLA"]["sha256"] == sha256_file(str(tmp / "data" / "TSLA.csv"))
    assert "TSLA" in App(str(tmp / "data"), str(tmp / "res2")).datasets


def test_refresh_replaces_data_and_changes_run_ids(srv):
    app, base, _ = srv
    post(base, "/api/datasets", {"symbol": "NVDA"})
    a = post(base, "/runs", {"symbol": "NVDA", "luck_test": {"trials": 0}})["run_id"]
    app.fetcher = fake_fetcher(app.calls, n=420, seed=10)      # newer prices
    post(base, "/api/datasets", {"symbol": "NVDA"})
    b = post(base, "/runs", {"symbol": "NVDA", "luck_test": {"trials": 0}})
    assert b["run_id"] != a and b["summary"]["days"] == 420 and not b["cached"]


@pytest.mark.parametrize("body", [{"symbol": "../etc"}, {"symbol": ""}, {"symbol": "A" * 16},
                                  {"symbol": "AAPL", "start": "2015-13-01"},
                                  {"symbol": "AAPL", "start": "2020-01-01", "end": "2019-01-01"},
                                  {"symbol": "AAPL", "evil": 1}, ["AAPL"]])
def test_bad_requests_rejected(srv, body):
    app, base, _ = srv
    assert status(lambda: post(base, "/api/datasets", body)) == 400
    assert app.calls == []


def test_failed_download_leaves_nothing(srv):
    app, base, tmp = srv

    def broken(symbol, start, end):
        raise fetch.FetchError("no prices found for ZZZZ")
    app.fetcher = broken
    assert status(lambda: post(base, "/api/datasets", {"symbol": "ZZZZ"})) == 400
    assert "ZZZZ" not in app.datasets and not os.path.exists(tmp / "data" / "ZZZZ.csv")
    assert "ZZZZ" not in load_manifest(str(tmp / "data"))


def test_public_server_refuses(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    app, httpd = serve("127.0.0.1", 0, str(d), str(tmp_path / "res"), public=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert status(lambda: post(base, "/api/datasets", {"symbol": "AAPL"})) == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("sym,ok", [("BRK-B", True), ("^GSPC", True), ("EURUSD=X", True),
                                    ("aapl", True), ("../etc", False), ("A/B", False), ("", False)])
def test_symbol_rule_shared_by_runs_and_downloads(sym, ok):
    try:
        runner.normalize({"symbol": sym})
        run_ok = True
    except runner.ConfigError:
        run_ok = False
    try:
        fetch.check_symbol(sym)
        fetch_ok = True
    except fetch.FetchError:
        fetch_ok = False
    assert run_ok == fetch_ok == ok


@pytest.fixture
def pub_dl(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    app, httpd = serve("127.0.0.1", 0, str(d), str(tmp_path / "res"), public=True, allow_downloads=True)
    app.calls = []
    app.fetcher = fake_fetcher(app.calls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_public_server_can_opt_in_with_limits(pub_dl, monkeypatch):
    app, base = pub_dl
    health = json.loads(urllib.request.urlopen(base + "/health").read())
    assert health["public"] is True and health["downloads"] is True
    assert post(base, "/api/datasets", {"symbol": "AAA"})["rows"] == 400
    assert app.calls[0] == ("AAA", "2005-01-01", None)            # default history covers 2008
    codes = [status(lambda s=s: post(base, "/api/datasets", {"symbol": s})) for s in ("BBB", "CCC", "DDD", "EEE", "FFF")]
    assert codes == [200, 200, 200, 200, 429]                       # 5 downloads a minute per visitor
    app.download_limit.per = 100
    monkeypatch.setitem(server_mod.PUBLIC, "max_datasets", 5)
    assert status(lambda: post(base, "/api/datasets", {"symbol": "GGG"})) == 403   # demo is full
    assert status(lambda: post(base, "/api/datasets", {"symbol": "AAA"})) == 200   # updating is fine


def test_local_health_reports_downloads(srv):
    _, base, _ = srv
    h = json.loads(urllib.request.urlopen(base + "/health").read())
    assert h["public"] is False and h["downloads"] is True
