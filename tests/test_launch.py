"""The one-click launcher: reuses a running copy instead of starting a second
one, never lets an older copy answer in its place, fetches starter stocks on
the first run only, and keeps going when packages can't be installed."""
import json
import os
import socket
import stat
import threading
import urllib.request

import pytest

from risklens import fetch, launch, server
from risklens.data import register, synthetic, write_csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_data(tmp_path, source="synthetic seed=4"):
    d = tmp_path / "data"
    d.mkdir()
    write_csv(str(d / "S.csv"), synthetic(300, seed=4))
    register(str(d), "S", "S.csv", source=source)
    return d


def running(app):
    httpd = server.bind(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def stop(httpd):
    httpd.shutdown()
    httpd.server_close()


def test_health_tells_copies_apart(tmp_path):
    d = make_data(tmp_path)
    app = server.App(str(d), str(tmp_path / "res"))
    httpd, port = running(app)
    try:
        h = launch.probe(port)
        assert h["build"] == server.BUILD and h["home"] == server.home_id(str(d))
        # this copy, same data folder: reuse it
        assert launch.survey([port], server.BUILD, server.home_id(str(d))) == (port, [])
        # different code (an older copy) or another folder: start our own
        assert launch.survey([port], "older-build", server.home_id(str(d))) == (None, [port])
        assert launch.survey([port], server.BUILD, "other-folder") == (None, [port])
    finally:
        stop(httpd)


def test_busy_port_is_skipped_not_reused(tmp_path):
    d = make_data(tmp_path)
    app = server.App(str(d), str(tmp_path / "res"))
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        assert not launch.port_free(busy)
        assert launch.probe(busy, timeout=0.3) is None            # not RiskLens
        httpd, port = launch.bind_first_free(server, app, [busy, 0])
        assert httpd is not None and port != busy
        httpd.server_close()
        assert launch.bind_first_free(server, app, [busy]) == (None, None)
    finally:
        blocker.close()


def test_starter_stocks_first_run_only(tmp_path):
    d = make_data(tmp_path)
    app = server.App(str(d), str(tmp_path / "res"))
    calls = []
    app.fetcher = lambda sym, start, end: calls.append(sym) or synthetic(400, seed=len(calls))
    assert launch.get_starter_stocks(app, ["AAA", "BBB"]) == ["AAA", "BBB"]
    assert set(app.datasets) == {"S", "AAA", "BBB"}
    assert launch.get_starter_stocks(app, ["CCC"]) == []           # has real stocks now
    assert calls == ["AAA", "BBB"]


def test_starter_stops_at_once_when_offline(tmp_path):
    d = make_data(tmp_path)
    app = server.App(str(d), str(tmp_path / "res"))
    calls = []

    def offline(sym, start, end):
        calls.append(sym)
        raise fetch.FetchError("Yahoo Finance request failed: no internet")
    app.fetcher = offline
    assert launch.get_starter_stocks(app, ["AAA", "BBB", "CCC"]) == []
    assert calls == ["AAA"]                                         # no long wait offline
    assert set(app.datasets) == {"S"}


def test_no_starter_download_when_user_already_has_stocks(tmp_path):
    d = make_data(tmp_path, source="Yahoo Finance via yfinance")
    app = server.App(str(d), str(tmp_path / "res"))
    app.fetcher = lambda *a: pytest.fail("should not download")
    assert launch.get_starter_stocks(app) == []


def test_failed_install_does_not_stop_the_app(monkeypatch, capsys):
    monkeypatch.setattr(launch, "missing_packages", lambda: ["yfinance"])
    cmds = []
    assert launch.install_packages(["yfinance"], run=lambda c: cmds.append(c) or 1) is False
    assert cmds[0][1:5] == ["-m", "pip", "install", "--disable-pip-version-check"]
    assert "pip install -r requirements.txt" in capsys.readouterr().out
    monkeypatch.setattr(launch, "missing_packages", lambda: [])
    assert launch.install_packages(["yfinance"], run=lambda c: 0) is True


def test_old_python_gets_a_clear_message(capsys):
    assert launch.python_ok((3, 9, 0)) is False
    assert "python.org" in capsys.readouterr().out
    assert launch.python_ok((3, 10, 0)) and launch.python_ok((3, 14, 1))


def test_launch_starts_then_reuses(tmp_path, monkeypatch):
    d = make_data(tmp_path)
    monkeypatch.setattr(launch, "DATA", str(d))
    monkeypatch.setattr(launch, "RESULTS", str(tmp_path / "res"))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    args = ["--port", str(port), "--no-browser", "--no-install", "--no-starter"]
    httpd = launch.main(args, wait=False)
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert json.loads(urllib.request.urlopen(url + "/health", timeout=5).read())["build"] == server.BUILD
        assert b"RiskLens" in urllib.request.urlopen(url + "/", timeout=5).read()
        assert launch.main(args, wait=False) == 0                   # second double-click: reuse
    finally:
        stop(httpd)


def test_start_files():
    with open(os.path.join(ROOT, "Start RiskLens.bat"), "rb") as f:
        bat = f.read()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")  # Windows line endings
    assert all(b < 128 for b in bat)
    text = bat.decode()
    assert 'pushd "%~dp0"' in text and "-m risklens.launch" in text and "Extract All" in text
    sh = os.path.join(ROOT, "start.command")
    assert os.stat(sh).st_mode & stat.S_IXUSR
    with open(sh, "rb") as f:
        body = f.read()
    assert body.startswith(b"#!/bin/sh") and b"\r" not in body and b"-m risklens.launch" in body
