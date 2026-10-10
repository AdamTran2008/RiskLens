"""Self-contained HTML risk report (inline SVG charts, no external files).

Shows, for up to 4 runs side by side: equity curve, drawdown curve,
a metrics table, the luck test, risk-rule events and the run manifests.
"""
from __future__ import annotations

import html
import json
import math

SERIES = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"),
          ("#1baf7a", "#199e70"), ("#6250d6", "#9085e9")]
W, PAD_L, PAD_R, PAD_T, PAD_B = 920, 64, 16, 14, 26


def label(run) -> str:
    c = run["config"]
    s = c["strategy"]
    p = ",".join(f"{k}={v}" for k, v in s["params"].items())
    r = c["risk"]
    rules = []
    if r["max_exposure"] < 1:
        rules.append(f"cap {r['max_exposure']:.0%}")
    if r["vol_target"]:
        rules.append(f"vol target {r['vol_target']:.0%}")
    if r["max_drawdown"]:
        rules.append(f"breaker {r['max_drawdown']:.0%}")
    return f"{s['name']}{'(' + p + ')' if p else ''} · " + (" + ".join(rules) if rules else "no risk rules")


def _nice(lo, hi, n=5):
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.001:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _down(xs, k=700):
    if len(xs) <= k:
        return list(range(len(xs)))
    step = len(xs) / k
    idx = sorted({int(i * step) for i in range(k)} | {len(xs) - 1})
    return idx


def _chart(cid, runs, key, height, fmt, floor_zero=False, invert=False):
    dates = runs[0]["series"]["dates"]
    idx = _down(dates)
    vals = []
    for r in runs:
        s = r["series"][key]
        vals.append([(-s[i] if invert else s[i]) for i in idx])
    lo = min(min(v) for v in vals)
    hi = max(max(v) for v in vals)
    if floor_zero:
        hi = max(hi, 0.0)
    ticks = _nice(lo, hi)
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    pw, ph = W - PAD_L - PAD_R, height - PAD_T - PAD_B
    X = lambda j: PAD_L + pw * j / max(1, len(idx) - 1)
    Y = lambda v: PAD_T + ph * (1 - (v - lo) / (hi - lo))
    out = [f'<svg id="{cid}" viewBox="0 0 {W} {height}" role="img" class="chart" '
           f'preserveAspectRatio="none" aria-label="{html.escape(key)} chart">']
    for t in ticks:
        y = Y(t)
        out.append(f'<line x1="{PAD_L}" x2="{W-PAD_R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{PAD_L-8}" y="{y+4:.1f}" class="tick" text-anchor="end">{fmt(t)}</text>')
    last_year = None
    for j, i in enumerate(idx):
        yr = dates[i][:4]
        if yr != last_year:
            if last_year is not None:
                out.append(f'<text x="{X(j):.1f}" y="{height-6}" class="tick" '
                           f'text-anchor="middle">{yr}</text>')
            last_year = yr
    for k, v in enumerate(vals):
        pts = " ".join(f"{X(j):.1f},{Y(x):.1f}" for j, x in enumerate(v))
        out.append(f'<polyline points="{pts}" class="s{k}" fill="none" stroke-width="2" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
    out.append(f'<line class="xhair" x1="0" x2="0" y1="{PAD_T}" y2="{PAD_T+ph}" visibility="hidden"/>')
    out.append(f'<rect class="hit" x="{PAD_L}" y="{PAD_T}" width="{pw}" height="{ph}" fill="transparent"/>')
    out.append("</svg>")
    data = {"dates": [dates[i] for i in idx], "vals": vals, "padl": PAD_L, "pw": pw, "w": W}
    return "\n".join(out), data


def _pct(x):
    return "—" if x is None else f"{x*100:.1f}%"


def render(runs: list[dict]) -> str:
    labels = [label(r) for r in runs]
    eq_svg, eq_data = _chart("eq", runs, "equity", 300,
                             lambda v: f"${v/100/1000:,.0f}k")
    dd_svg, dd_data = _chart("dd", runs, "drawdown", 180,
                             lambda v: f"{v*100:.0f}%" if abs(v) > 1e-12 else "0%",
                             floor_zero=True, invert=True)
    rows = [
        ("Final equity", lambda s, r: f"${s['final_equity']:,.0f}"),
        ("Total return", lambda s, r: _pct(s["total_return"])),
        ("CAGR", lambda s, r: _pct(s["cagr"])),
        ("Volatility (ann.)", lambda s, r: _pct(s["volatility"])),
        ("Sharpe (rf=0)", lambda s, r: f"{s['sharpe']:.2f}"),
        ("Sortino", lambda s, r: f"{s['sortino']:.2f}"),
        ("Max drawdown", lambda s, r: _pct(-s["max_drawdown"])),
        ("Max DD window", lambda s, r: f"{s['max_drawdown_peak']} → {s['max_drawdown_trough']}"),
        ("Days to recover", lambda s, r: "not recovered" if s["drawdown_recovery_days"] is None
         and s["max_drawdown"] > 0 else str(s["drawdown_recovery_days"])),
        ("1-day VaR 95%", lambda s, r: _pct(-s["var_95"])),
        ("1-day CVaR 95%", lambda s, r: _pct(-s["cvar_95"])),
        ("Worst day", lambda s, r: _pct(-s["worst_day"])),
        ("Time in market", lambda s, r: _pct(s["time_in_market"])),
        ("Trades / fees", lambda s, r: f"{s['trades']} / ${s['fees_paid']:,.0f}"),
        ("Luck test p-value", lambda s, r: f"{r['luck_test']['p_value']:.3f}" if r["luck_test"] else "—"),
        ("Risk-rule events", lambda s, r: str(sum(1 for e in r["risk_events"]
                                                  if e[1] != "CASH_CLIP"))),
    ]
    head = "".join(f'<th><span class="key s{k}bg"></span>{html.escape(l)}</th>'
                   for k, l in enumerate(labels))
    body = "".join("<tr><td>" + n + "</td>" +
                   "".join(f"<td>{html.escape(f(r['summary'], r))}</td>" for r in runs) + "</tr>"
                   for n, f in rows)
    luck = []  # one line per run
    for k, r in enumerate(runs):
        lt = r["luck_test"]
        if not lt:
            continue
        verdict = ("timing beats random at the 5% level" if lt["p_value"] < 0.05
                   else "consistent with luck: random timing did as well")
        luck.append(f'<li><span class="key s{k}bg"></span><b>{html.escape(labels[k])}</b>: '
                    f'gross Sharpe {lt["actual_sharpe_gross"]:.2f} vs random median '
                    f'{lt["random_median_sharpe"]:.2f} (95th pct {lt["random_95th_sharpe"]:.2f}) '
                    f'over {lt["trials"]} shuffles → p = {lt["p_value"]:.3f}, {verdict}.</li>')
    events = []
    for k, r in enumerate(runs):
        ev = [e for e in r["risk_events"] if e[1] != "CASH_CLIP"]
        if ev:
            items = "".join(f"<li><code>{html.escape(e[0])}</code> {html.escape(e[1])}: "
                            f"{html.escape(e[2])}</li>" for e in ev[:30])
            more = f"<li>… {len(ev)-30} more</li>" if len(ev) > 30 else ""
            events.append(f"<h3><span class='key s{k}bg'></span>{html.escape(labels[k])}</h3>"
                          f"<ul>{items}{more}</ul>")
    manifests = "".join(
        f"<tr><td><span class='key s{k}bg'></span><code>{r['run_id']}</code></td>"
        f"<td><code>{r['manifest']['dataset_sha256'][:16]}…</code></td>"
        f"<td><code>{r['manifest']['engine_version']}</code></td>"
        f"<td><code>{r['manifest']['result_fingerprint'][:16]}…</code></td></tr>"
        for k, r in enumerate(runs))
    c0 = runs[0]["config"]
    legend = "".join(f'<span class="leg"><span class="key s{k}bg"></span>{html.escape(l)}</span>'
                     for k, l in enumerate(labels))
    colors_light = "".join(f".s{k}{{stroke:{c[0]}}}.s{k}bg{{background:{c[0]}}}" for k, c in enumerate(SERIES))
    colors_dark = "".join(f".s{k}{{stroke:{c[1]}}}.s{k}bg{{background:{c[1]}}}" for k, c in enumerate(SERIES))
    data_json = json.dumps({"eq": eq_data, "dd": dd_data, "labels": labels})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RiskLens report · {html.escape(c0['symbol'])}</title>
<style>
:root{{color-scheme:light;--bg:#fcfcfb;--fg:#0b0b0b;--muted:#52514e;--grid:#e6e5e0;--card:#f4f3ef}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#1a1a19;--fg:#f0efec;--muted:#c3c2b7;--grid:#34332f;--card:#24241f}}}}
{colors_light}
@media (prefers-color-scheme:dark){{{colors_dark}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 8px}} h3{{font-size:14px;margin:12px 0 4px}}
.sub{{color:var(--muted);margin:0 0 16px}}
.chart{{width:100%;height:auto;display:block}}
.grid{{stroke:var(--grid);stroke-width:1}} .tick{{fill:var(--muted);font-size:11px}}
.xhair{{stroke:var(--muted);stroke-width:1}}
.key{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:baseline}}
.leg{{margin-right:18px;color:var(--fg);font-size:13px}}
.wrap{{position:relative}}
.tip{{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--grid);border-radius:6px;padding:6px 8px;font-size:12px;display:none;white-space:nowrap}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid)}}
th{{font-weight:600}} td:first-child{{color:var(--muted)}} code{{font-size:12px}}
ul{{padding-left:18px;margin:4px 0}} li{{margin:2px 0}}
.note{{background:var(--card);border-radius:8px;padding:10px 14px;color:var(--muted);font-size:13px}}
</style></head><body><main>
<h1>RiskLens risk report · {html.escape(c0['symbol'])}</h1>
<p class="sub">{html.escape(runs[0]['summary']['start'])} → {html.escape(runs[0]['summary']['end'])} ·
${c0['initial_cash']:,.0f} starting cash · fees {c0['costs']['fee_bps']} bps, slippage {c0['costs']['slippage_bps']} bps ·
orders decided at the close fill at the next open</p>
<div>{legend}</div>
<h2>Equity</h2><div class="wrap">{eq_svg}<div class="tip" id="eq-tip"></div></div>
<h2>Drawdown from peak</h2><div class="wrap">{dd_svg}<div class="tip" id="dd-tip"></div></div>
<h2>Metrics</h2><table><tr><th>Metric</th>{head}</tr>{body}</table>
<h2>Luck test: is the timing real?</h2><ul>{''.join(luck) or '<li>not run</li>'}</ul>
<p class="note">The strategy's daily exposure is shuffled across days many times. That keeps how much it was
invested but randomizes when. The p-value is the share of random timings that did at least as well. Gross of costs.</p>
<h2>Risk-rule events</h2>{''.join(events) or '<p>none</p>'}
<h2>Reproducibility</h2>
<table><tr><th>Run ID</th><th>Dataset SHA-256</th><th>Engine</th><th>Result fingerprint</th></tr>{manifests}</table>
<p class="note">Same config + same dataset hash + same engine version ⇒ same run ID and identical results.</p>
</main>
<script>
const D={data_json};
function fmtEq(v){{return '$'+Math.round(v/100).toLocaleString()}}
function fmtDd(v){{return (v*100).toFixed(1)+'%'}}
for (const [id,f] of [['eq',fmtEq],['dd',fmtDd]]) {{
  const svg=document.getElementById(id), tip=document.getElementById(id+'-tip'), d=D[id];
  const hit=svg.querySelector('.hit'), xh=svg.querySelector('.xhair');
  hit.addEventListener('mousemove',e=>{{
    const r=svg.getBoundingClientRect(), sx=(e.clientX-r.left)*d.w/r.width;
    const j=Math.max(0,Math.min(d.dates.length-1,Math.round((sx-d.padl)/d.pw*(d.dates.length-1))));
    const x=d.padl+d.pw*j/Math.max(1,d.dates.length-1);
    xh.setAttribute('x1',x);xh.setAttribute('x2',x);xh.setAttribute('visibility','visible');
    tip.innerHTML='<b>'+d.dates[j]+'</b><br>'+d.vals.map((v,k)=>'<span class="key s'+k+'bg"></span>'+f(v[j])).join('<br>');
    tip.style.display='block';
    const left=(x/d.w)*r.width; tip.style.left=Math.min(left+12,r.width-160)+'px'; tip.style.top='8px';
  }});
  hit.addEventListener('mouseleave',()=>{{tip.style.display='none';xh.setAttribute('visibility','hidden')}});
}}
</script></body></html>"""
