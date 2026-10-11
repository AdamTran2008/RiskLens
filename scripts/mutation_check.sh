#!/usr/bin/env bash
# Mutation check: plant realistic bugs one at a time and confirm the test
# suite catches every one. Evidence that the tests actually have teeth.
set -u
cd "$(dirname "$0")/.."
BK=$(mktemp -d)
cp risklens/*.py "$BK/"
restore() { cp "$BK"/*.py risklens/; }
trap 'restore; rm -rf "$BK"' EXIT

caught=0; total=0
mutate() {  # description file sed-expression
  total=$((total+1))
  sed -i "$3" "risklens/$2"
  if cmp -s "risklens/$2" "$BK/$2"; then
    echo "NOT APPLIED $1"; restore; return
  fi
  if python3 -m pytest -q -x -p no:cacheprovider tests >/dev/null 2>&1; then
    echo "SURVIVED  $1"
  else
    echo "caught    $1"; caught=$((caught+1))
  fi
  restore
}

mutate "look-ahead: strategy sees tomorrow's bar"   engine.py  's/        seen.append(bar)                   # today becomes visible, never tomorrow/        seen.append(bars[t + 1] if t + 1 < len(bars) else bar)/'
mutate "fill at the close instead of next open"     engine.py  's/px = cents(bar.open \* (1 + slip))/px = cents(bar.close * (1 + slip))/'
mutate "buy fee not charged to cash"                engine.py  's/                    cash -= notional + fee/                    cash -= notional/'
mutate "no cash check (allows leverage)"            engine.py  's/qty = min(pending, int(cash \/ (px \* (1 + fee_rate))))/qty = pending/; s/while qty > 0 and qty \* px + fee_of(qty \* px) > cash:/while False:/'
mutate "drawdown breaker ignored"                   engine.py  's/^        if halted:$/        if False:/'
mutate "exposure cap ignored"                       engine.py  's/        tgt = min(tgt, risk.max_exposure)/        tgt = tgt/'
mutate "vol targeting ignored"                      engine.py  's/                tgt = tgt \* min(1.0, risk.vol_target \/ rv)/                tgt = tgt/'
mutate "peak equity never updated"                  engine.py  's/        if equity > peak:/        if False:/'
mutate "VaR off by one"                             metrics.py 's/    var = -s\[k - 1\]/    var = -s[k]/'
mutate "luck test counts ties wrong"                metrics.py 's/        if s >= actual - 1e-12:/        if s > actual + 1e-12:/'
mutate "VaR forecast peeks at the day it forecasts" metrics.py 's/        out.append(-win\[k - 1\])/        out.append(-sorted(returns[t - window + 1:t + 1])[k - 1])/'
mutate "Kupiec p-value from the wrong distribution" metrics.py 's/    return math.erfc(math.sqrt(max(x, 0.0) \/ 2))/    return math.exp(-max(x, 0.0) \/ 2)/'
mutate "run id ignores the dataset hash"            runner.py  's/"data": dataset_sha,/"data": "",/'

echo "$caught/$total mutations caught"
[ "$caught" -eq "$total" ]
