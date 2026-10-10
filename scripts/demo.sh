#!/usr/bin/env bash
# RiskLens end-to-end demo (~10 s):
#   1. make sure datasets exist (synthetic always; real AAPL/SPY if you fetched them)
#   2. start the run server
#   3. the client submits the same strategy WITHOUT and WITH risk rules
#   4. resubmit: same run ID comes back from the result store (reproducible)
#   5. an HTML risk report is written to reports/
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${PORT:-8765}
SYMBOL=${1:-}
[ -f data/SYNTH.csv ] || python3 -m risklens.data --data-dir data
if [ -z "$SYMBOL" ]; then
  if [ -f data/AAPL.csv ]; then SYMBOL=AAPL; else SYMBOL=SYNTH; fi
fi

python3 -m risklens.server --port "$PORT" > /tmp/risklens-server.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for _ in $(seq 50); do
  python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:$PORT/health')" 2>/dev/null && break
  sleep 0.1
done
head -n 1 /tmp/risklens-server.log
C="python3 -m risklens.client --server http://127.0.0.1:$PORT"

echo; echo "### datasets on the server"
$C datasets

echo; echo "### $SYMBOL: SMA(50/200) trend strategy, without vs with risk rules"
echo "    risk rules = 15% volatility target + 15% drawdown circuit breaker"
$C compare "$SYMBOL" --strategy sma_cross --fast 50 --slow 200 --vol-target 0.15 --max-drawdown 0.15

echo; echo "### resubmit the same runs: served from the result store, same run IDs"
$C compare "$SYMBOL" --strategy sma_cross --fast 50 --slow 200 --vol-target 0.15 --max-drawdown 0.15 | head -n 2
echo; echo "Open the report file above in a browser to see the charts."
