"""Start RiskLens with one double-click.

    Windows       double-click "Start RiskLens.bat"
    Mac           double-click start.command (the first time: right-click > Open)
    Linux, any    ./start.command    or    python -m risklens.launch

What it does, in order:
  1. checks the Python version;
  2. installs the two packages RiskLens uses if they are missing (first run);
  3. if this copy of RiskLens is already running, just opens your browser;
  4. otherwise starts it on the first free port from 8765 (so an older copy
     still running in another window can't answer in its place);
  5. first run only: downloads a few well-known stocks so the app isn't empty;
  6. opens the app in your browser.

This file must stay importable by old Pythons (no newer syntax) so step 1
can explain the problem instead of crashing.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import site
import socket
import subprocess
import sys
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RESULTS = os.path.join(ROOT, "results")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
MIN_PY = (3, 10)
NEEDS = {"websocket": "websocket-client", "yfinance": "yfinance"}   # import name: package
STARTER = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]
FIRST_PORT = 8765
PORT_TRIES = 10
LINE = "-" * 66


def say(msg=""):
    print(msg, flush=True)


def python_ok(version=None) -> bool:
    v = version or sys.version_info
    if tuple(v[:2]) >= MIN_PY:
        return True
    say("RiskLens needs Python %d.%d or newer; this is Python %d.%d." % (MIN_PY + tuple(v[:2])))
    say("Get the latest Python from https://www.python.org/downloads/ and start RiskLens again.")
    return False


# ------------------------------------------------------------- packages
def missing_packages() -> list:
    return [pkg for mod, pkg in NEEDS.items() if importlib.util.find_spec(mod) is None]


def _refresh_paths():
    """Make packages pip just installed importable in this same process (a
    user-level install can create a folder Python didn't know at startup)."""
    try:
        user = site.getusersitepackages()
        if os.path.isdir(user) and user not in sys.path:
            site.addsitedir(user)
    except Exception:
        pass
    importlib.invalidate_caches()


def install_packages(missing, run=subprocess.call) -> bool:
    say("First run: installing %s (needs internet, about a minute)..." % ", ".join(missing))
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS]
    try:
        code = run(cmd)
        if code != 0 and importlib.util.find_spec("pip") is None:
            # some Python installs come without pip; Python can add it itself
            if run([sys.executable, "-m", "ensurepip", "--upgrade"]) == 0:
                code = run(cmd)
    except OSError:
        code = 1
    _refresh_paths()
    still = missing_packages()
    if code == 0 and not still:
        say("Installed.")
        return True
    say("")
    say("Could not install %s automatically. RiskLens still works, but the" % ", ".join(still or missing))
    say("live market feed and downloading new stocks need it. To install by hand, run:")
    say('  "%s" -m pip install -r requirements.txt' % sys.executable)
    say("")
    return False


# ---------------------------------------------------------------- ports
def port_free(port, host="127.0.0.1") -> bool:
    """True if nothing is listening on host:port (checked by binding, which
    is instant; connecting to a closed port can take seconds on Windows)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":          # same rule as the server: ignore old closed connections
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def probe(port, timeout=1.0):
    """The /health answer of a RiskLens server on this port, or None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a proxy
    try:
        with opener.open("http://127.0.0.1:%d/health" % port, timeout=timeout) as r:
            h = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return h if isinstance(h, dict) and h.get("ok") is True else None


def survey(ports, build, home):
    """Look at the ports in use: (port where this same copy already runs or
    None, ports held by other RiskLens servers)."""
    others = []
    for p in ports:
        if port_free(p):
            continue
        h = probe(p)
        if h is None:
            continue
        if h.get("build") == build and h.get("home") == home:
            return p, others
        others.append(p)
    return None, others


def bind_first_free(server, app, ports):
    """(httpd, port) on the first port that can be bound, or (None, None)."""
    for p in ports:
        try:
            httpd = server.bind(app, "127.0.0.1", p)
        except OSError:
            continue
        return httpd, httpd.server_address[1]
    return None, None


# ------------------------------------------------------------- starter
def get_starter_stocks(app, symbols=STARTER) -> list:
    """First run only (no real stocks yet): download a few well-known ones.
    Stops at once if the first download fails (offline, Yahoo busy)."""
    if not app.downloads:
        return []
    if any(not str(e.get("source", "")).startswith("synthetic") for e in app.datasets.values()):
        return []
    say("Getting a few stocks to start with (first run only): " + ", ".join(symbols))
    got = []
    for sym in symbols:
        try:
            e = app.add_dataset({"symbol": sym})
        except Exception as err:          # offline, rate limited, package missing...
            say("  %s: skipped (%s)" % (sym, str(err)[:100]))
            if not got:
                say("  Skipping the rest; you can add any stock later from the search box.")
                break
            continue
        got.append(sym)
        say("  %s: %s trading days, %s to %s" % (sym, format(e["rows"], ","), e["start"], e["end"]))
    return got


# ---------------------------------------------------------------- start
def open_browser(url):
    try:
        import webbrowser
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    say("Couldn't open a browser by itself: paste %s into your browser." % url)
    return False


def banner(url, others):
    say("")
    say(LINE)
    say("  RiskLens is running:  " + url)
    say("  Your browser opens by itself. If it doesn't, paste that address into it.")
    say("")
    say("  Keep this window open while you use RiskLens.")
    say("  To stop RiskLens, close this window (or press Ctrl+C).")
    if others:
        say("")
        say("  Note: another copy of RiskLens (maybe an older one) is also running,")
        say("  at " + ", ".join("http://127.0.0.1:%d/" % p for p in others) + ". You can close its window.")
    say(LINE)


def _quiet_libraries():
    """Third-party libraries log retries and warnings to this window; the app
    reports problems on the page instead."""
    import logging
    for name in ("yfinance", "websocket", "peewee", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def main(argv=None, wait=True):
    """Returns the exit code (or, with wait=False, the running server)."""
    ap = argparse.ArgumentParser(description="Start RiskLens and open it in your browser.")
    ap.add_argument("--port", type=int, default=FIRST_PORT, help="first port to try (default 8765)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser")
    ap.add_argument("--no-install", action="store_true", help="don't install missing packages")
    ap.add_argument("--no-starter", action="store_true", help="don't download starter stocks")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # never crash on a console that can't show a character
        except Exception:
            pass

    say("Starting RiskLens...")
    if not python_ok():
        return 1
    if ROOT not in sys.path:                      # also works as: python risklens/launch.py
        sys.path.insert(0, ROOT)
    missing = missing_packages()
    if missing and not a.no_install:
        install_packages(missing)
    from risklens import server                   # only now: needs Python 3.10+
    _quiet_libraries()

    ports = list(range(a.port, a.port + PORT_TRIES))
    running, others = survey(ports, server.BUILD, server.home_id(DATA))
    if running:
        url = "http://127.0.0.1:%d/" % running
        say("RiskLens is already running at %s (in another window). Opening it." % url)
        if not a.no_browser:
            open_browser(url)
        return 0

    try:
        app = server.App(DATA, RESULTS)
    except SystemExit as e:                       # e.g. a data file changed since it was saved
        say(str(e))
        return 1
    # the usual ports first; if all are taken (or reserved by Windows), any free one
    httpd, port = bind_first_free(server, app, [p for p in ports if p not in others] + [0])
    if httpd is None:
        say("Couldn't open a port for the app. Close other RiskLens windows and try again.")
        return 1
    url = "http://127.0.0.1:%d/" % port
    t = threading.Thread(target=httpd.serve_forever, name="risklens-server", daemon=True)
    t.start()                                     # answer at once, even during the downloads below

    if not a.no_starter:
        get_starter_stocks(app)
    banner(url, others)
    if not a.no_browser:
        open_browser(url)
    if not wait:                                  # tests: hand back the running server
        return httpd
    try:
        while t.is_alive():
            t.join(0.5)                           # short waits keep Ctrl+C working on Windows
    except KeyboardInterrupt:
        say("Stopping RiskLens...")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
