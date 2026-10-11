#!/bin/sh
# RiskLens: double-click on a Mac (the first time: right-click > Open), or
# run ./start.command on Linux. Starts the app and opens your browser.
# The first run installs two Python packages and downloads a few stocks.
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "RiskLens needs Python 3.10 or newer. Get it from https://www.python.org/downloads/"
  exit 1
fi

# Use this Python if it already has the two packages. Otherwise keep them in a
# private .venv folder here: no admin rights, nothing installed system-wide.
if ! "$PY" -c 'import websocket, yfinance' >/dev/null 2>&1; then
  if [ ! -x .venv/bin/python ]; then
    echo "First run: making a private Python environment in .venv ..."
    "$PY" -m venv .venv >/dev/null 2>&1 || rm -rf .venv
  fi
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
fi

exec "$PY" -m risklens.launch "$@"
