"""Self-contained HTML risk report (inline SVG, no external files needed).

Layout, top to bottom:
  verdict sentence   what the risk rules changed, in plain words
  underwater chart   drawdown from peak, with circuit-breaker trips marked
  comparison strip   max drawdown, volatility, Sharpe, annual return
  equity chart       growth of the starting cash
  exposure chart     share of equity held in the stock each day
  metrics table, luck test, risk-rule log, reproducibility details

Colors carry meaning: the run WITHOUT risk rules is graphite gray, the run
WITH risk rules is teal, and amber is reserved for risk-rule events.
"""
from __future__ import annotations

import html
import json
import math

# (light, dark) per series slot. Slot 0 = baseline gray, slot 1 = teal.
SERIES = [("#7a8494", "#9aa4b1"), ("#0f7b7b", "#3fb8af"),
          ("#3f51b5", "#8c9eff"), ("#8e3b8e", "#d58fd5")]
VW = 1000                    # SVG viewBox width
PL, PR, PT, PB = 58, 12, 12, 26


def esc(s) -> str:
    return html.escape(str(s))


# ----------------------------------------------------------------- labels
def has_rules(run) -> bool:
    r = run["config"]["risk"]
    return bool(r["vol_target"] or r["max_drawdown"] or r["max_exposure"] < 1)


def strategy_name(run) -> str:
    s = run["config"]["strategy"]
    p = s["params"]
    if s["name"] == "sma_cross":
        return f"SMA {p['fast']}/{p['slow']} trend"
    if s["name"] == "momentum":
        return f"{p['lookback']}-day momentum"
    return "Buy and hold"


def rules_text(run) -> str:
    r = run["config"]["risk"]
    parts = []
    if r["vol_target"]:
        parts.append(f"{r['vol_target']:.0%} volatility target")
    if r["max_drawdown"]:
        parts.append(f"{r['max_drawdown']:.0%} drawdown breaker")
    if r["max_exposure"] < 1:
        parts.append(f"{r['max_exposure']:.0%} exposure cap")
    return ", ".join(parts)


def label(run) -> str:
    return f"{strategy_name(run)}, " + (f"with {rules_text(run)}" if has_rules(run)
                                         else "no risk rules")


def short_label(run) -> str:
    return "With risk rules" if has_rules(run) else "No risk rules"


# ---------------------------------------------------------------- formats
def pct(x, digits=1, signed=False):
    if x is None:
        return "–"
    s = f"{x * 100:+.{digits}f}%" if signed else f"{x * 100:.{digits}f}%"
    return s.replace("-", "−")


def loss(x, digits=1):
    """A loss stored as a positive fraction, shown as a negative percent."""
    return pct(-x, digits) if x else "0.0%"


def month_year(d):
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(d[5:7]) - 1]} {d[:4]}"


# ----------------------------------------------------------------- charts
def _nice_ticks(lo, hi, n=4):
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t = math.floor(lo / step) * step
    out = []
    while t <= hi + step * 1e-6:
        out.append(round(t, 10))
        t += step
    return out


def _sample(n, k=800):
    if n <= k:
        return list(range(n))
    return sorted({int(i * n / k) for i in range(k)} | {n - 1})


def _chart(cid, runs, values, height, fmt, area=False, y_floor=None, y_ceil=None,
           events=True):
    """values: list (per run) of full-length numeric series."""
    dates = runs[0]["series"]["dates"]
    idx = _sample(len(dates))
    vals = [[v[i] for i in idx] for v in values]
    lo = min(min(v) for v in vals)
    hi = max(max(v) for v in vals)
    if y_floor is not None:
        lo = min(lo, y_floor)
    if y_ceil is not None:
        hi = max(hi, y_ceil)
    ticks = _nice_ticks(lo, hi)
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    pw, ph = VW - PL - PR, height - PT - PB
    X = lambda j: PL + pw * j / max(1, len(idx) - 1)
    Y = lambda v: PT + ph * (1 - (v - lo) / (hi - lo or 1))
    pos = {d: j for j, d in enumerate(dates[i] for i in idx)}

    def nearest(date):
        if date in pos:
            return pos[date]
        for j, i in enumerate(idx):
            if dates[i] >= date:
                return j
        return len(idx) - 1

    g = [f'<svg id="{cid}" class="chart" viewBox="0 0 {VW} {height}" role="img">']
    # halt periods (trip -> resume) shaded first, behind everything
    if events:
        for k, r in enumerate(runs):
            trip = None
            for d, kind, _ in r["risk_events"]:
                if kind == "DRAWDOWN_BREAKER":
                    trip = d
                elif kind == "RESUME" and trip:
                    x1, x2 = X(nearest(trip)), X(nearest(d))
                    g.append(f'<rect class="halt" x="{x1:.1f}" y="{PT}" width="{max(2, x2 - x1):.1f}" '
                             f'height="{ph}"/>')
                    trip = None
    for t in ticks:
        y = Y(t)
        g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{PL}" x2="{VW - PR}" '
                 f'y1="{y:.1f}" y2="{y:.1f}"/>'
                 f'<text class="tick" x="{PL - 8}" y="{y + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
    last = None
    step_years = max(1, round((int(dates[-1][:4]) - int(dates[0][:4]) + 1) / 8))
    for j, i in enumerate(idx):
        yr = dates[i][:4]
        if yr != last:
            if last is not None and int(yr) % step_years == 0:
                g.append(f'<line class="ytick" x1="{X(j):.1f}" x2="{X(j):.1f}" y1="{PT + ph}" '
                         f'y2="{PT + ph + 4}"/><text class="tick" x="{X(j):.1f}" '
                         f'y="{height - 6}" text-anchor="middle">{yr}</text>')
            last = yr
    zero_y = Y(max(lo, min(0.0, hi)))
    for k, v in enumerate(vals):
        pts = " ".join(f"{X(j):.1f},{Y(x):.1f}" for j, x in enumerate(v))
        if area:
            g.append(f'<polygon class="fill s{k}f" points="{PL:.1f},{zero_y:.1f} {pts} '
                     f'{X(len(v) - 1):.1f},{zero_y:.1f}"/>')
        g.append(f'<polyline class="line s{k}" points="{pts}"/>')
    if events:
        for k, r in enumerate(runs):
            for d, kind, _ in r["risk_events"]:
                if kind == "DRAWDOWN_BREAKER":
                    x = X(nearest(d))
                    g.append(f'<path class="trip" d="M{x:.1f},{PT} l-5,-0 l5,8 l5,-8 z"/>')
    g.append(f'<line class="xhair" x1="0" x2="0" y1="{PT}" y2="{PT + ph}" visibility="hidden"/>')
    g.append(f'<rect class="hit" x="{PL}" y="{PT}" width="{pw}" height="{ph}"/></svg>')
    data = {"dates": [dates[i] for i in idx], "vals": vals, "pl": PL, "pw": pw, "w": VW}
    return "".join(g), data


# ---------------------------------------------------------------- verdict
def verdict(runs) -> str:
    if len(runs) == 2 and not has_rules(runs[0]) and has_rules(runs[1]):
        a, b = runs[0]["summary"], runs[1]["summary"]
        dd = (f"the worst fall shrinks from {loss(a['max_drawdown'])} to "
              f"{loss(b['max_drawdown'])}" if b["max_drawdown"] < a["max_drawdown"]
              else f"the worst fall is {loss(b['max_drawdown'])} versus "
                   f"{loss(a['max_drawdown'])} without them")
        vol = (f"volatility drops from {pct(a['volatility'])} to {pct(b['volatility'])}"
               if b["volatility"] < a["volatility"]
               else f"volatility is {pct(b['volatility'])} versus {pct(a['volatility'])}")
        diff = b["cagr"] - a["cagr"]
        ret = (f"and annual return {'rises' if diff > 0 else 'falls'} from {pct(a['cagr'])} "
               f"to {pct(b['cagr'])}")
        return f"With risk rules, {dd}, {vol}, {ret}."
    if len(runs) == 1:
        s = runs[0]["summary"]
        return (f"Worst fall {loss(s['max_drawdown'])} "
                f"({month_year(s['max_drawdown_peak'])} to {month_year(s['max_drawdown_trough'])}), "
                f"annual return {pct(s['cagr'])}, volatility {pct(s['volatility'])}.")
    best = min(runs, key=lambda r: r["summary"]["max_drawdown"])
    return (f"Smallest worst fall: {esc(label(best))} at "
            f"{loss(best['summary']['max_drawdown'])}.")


# ---------------------------------------------------------------- tables
ROWS = [
    ("Return", [
        ("Final value", lambda s: f"${s['final_equity']:,.0f}", "final_equity", +1),
        ("Total return", lambda s: pct(s["total_return"]), "total_return", +1),
        ("Annual return (CAGR)", lambda s: pct(s["cagr"]), "cagr", +1),
    ]),
    ("Risk", [
        ("Max drawdown", lambda s: loss(s["max_drawdown"]), "max_drawdown", -1),
        ("Worst fall, peak to trough", lambda s: f"{s['max_drawdown_peak']} to "
         f"{s['max_drawdown_trough']}", None, 0),
        ("Days to recover", lambda s: "not recovered" if s["drawdown_recovery_days"] is None
         and s["max_drawdown"] > 0 else f"{s['drawdown_recovery_days']}", None, 0),
        ("Volatility (annual)", lambda s: pct(s["volatility"]), "volatility", -1),
        ("1-day VaR, 95%", lambda s: loss(s["var_95"], 2), "var_95", -1),
        ("1-day Expected Shortfall, 95%", lambda s: loss(s["cvar_95"], 2), "cvar_95", -1),
        ("Worst single day", lambda s: loss(s["worst_day"]), "worst_day", -1),
    ]),
    ("Risk-adjusted", [
        ("Sharpe ratio (rf = 0)", lambda s: f"{s['sharpe']:.2f}", "sharpe", +1),
        ("Sortino ratio", lambda s: f"{s['sortino']:.2f}", "sortino", +1),
    ]),
    ("Trading", [
        ("Time in the market", lambda s: pct(s["time_in_market"], 0), None, 0),
        ("Trades", lambda s: f"{s['trades']}", None, 0),
        ("Fees and slippage paid", lambda s: f"${s['fees_paid']:,.0f}", None, 0),
    ]),
]


def metrics_table(runs) -> str:
    head = "".join(f'<th class="num"><span class="sw s{k}bg"></span>{esc(short_label(r))}</th>'
                   for k, r in enumerate(runs))
    body = []
    for group, rows in ROWS:
        body.append(f'<tr class="group"><th colspan="{len(runs) + 1}">{group}</th></tr>')
        for name, f, key, better in rows:
            cells = []
            vals = [r["summary"][key] for r in runs] if key else None
            best = None
            if key and better and len(runs) > 1 and len(set(vals)) > 1:
                best = vals.index(max(vals) if better > 0 else min(vals))
            for k, r in enumerate(runs):
                cls = "num best" if k == best else "num"
                cells.append(f'<td class="{cls}">{esc(f(r["summary"]))}</td>')
            body.append(f"<tr><th>{name}</th>{''.join(cells)}</tr>")
    return (f'<div class="scroll"><table class="metrics"><thead><tr><th></th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def luck_block(runs) -> str:
    out = []
    for k, r in enumerate(runs):
        lt = r["luck_test"]
        if not lt:
            continue
        p = lt["p_value"]
        if p < 0.05:
            msg = (f"Only {p:.1%} of random timings did as well. The timing likely carries "
                   f"real information.")
        else:
            msg = (f"{p:.0%} of random timings did at least as well, so this result is "
                   f"consistent with luck.")
        out.append(
            f'<div class="luck"><p><span class="sw s{k}bg"></span><strong>{esc(short_label(r))}.</strong> '
            f'Sharpe {lt["actual_sharpe_gross"]:.2f} before costs, against a median of '
            f'{lt["random_median_sharpe"]:.2f} for {lt["trials"]:,} random timings. {msg}</p>'
            f'<div class="pbar" aria-label="p-value {p:.3f}"><span class="thr" style="left:5%"></span>'
            f'<span class="dot s{k}bg" style="left:{min(99.5, p * 100):.1f}%"></span></div>'
            f'<div class="pscale"><span>0</span><span class="thrlab">0.05</span>'
            f'<span>p = {p:.3f}</span><span>1</span></div></div>')
    return "".join(out) or "<p>The luck test was not run.</p>"


def events_block(runs) -> str:
    rows = []
    names = {"DRAWDOWN_BREAKER": "Circuit breaker", "RESUME": "Trading resumed",
             "UNPRICEABLE": "No order", "CASH_CLIP": "Order reduced"}
    for k, r in enumerate(runs):
        for d, kind, detail in r["risk_events"]:
            if kind == "CASH_CLIP":
                continue
            rows.append(f'<tr><td class="date">{esc(d)}</td><td><span class="sw s{k}bg"></span>'
                        f'{esc(names.get(kind, kind))}</td><td>{esc(detail)}</td></tr>')
    if not rows:
        return "<p>No risk rule had to step in.</p>"
    shown = rows[:40]
    more = (f'<p class="note">{len(rows) - 40} more events are in the run’s JSON result.</p>'
            if len(rows) > 40 else "")
    return (f'<div class="scroll"><table class="events"><thead><tr><th>Date</th><th>Event</th>'
            f'<th>Detail</th></tr></thead><tbody>{"".join(shown)}</tbody></table></div>{more}')


# ----------------------------------------------------------------- render
def render(runs: list[dict]) -> str:
    c0 = runs[0]["config"]
    s0 = runs[0]["summary"]
    dd_svg, dd_data = _chart("dd", runs, [[-x for x in r["series"]["drawdown"]] for r in runs],
                             300, lambda v: "0%" if abs(v) < 1e-12 else pct(v, 0),
                             area=True, y_ceil=0.0)
    eq_svg, eq_data = _chart("eq", runs, [[e / 100 for e in r["series"]["equity"]] for r in runs],
                             260, lambda v: f"${v / 1000:,.0f}k", events=False)
    expo = []
    for r in runs:
        s = r["series"]
        expo.append([(p * c / e if e else 0.0) for p, c, e in
                     zip(s["position"], s["close"], s["equity"])])
    ex_svg, ex_data = _chart("ex", runs, expo, 150, lambda v: pct(v, 0),
                             y_floor=0.0, y_ceil=1.0, events=False)

    strip = ""
    if len(runs) == 2:
        a, b = runs[0]["summary"], runs[1]["summary"]
        items = [("Max drawdown", loss(a["max_drawdown"]), loss(b["max_drawdown"]),
                  b["max_drawdown"] < a["max_drawdown"]),
                 ("Volatility", pct(a["volatility"]), pct(b["volatility"]),
                  b["volatility"] < a["volatility"]),
                 ("Sharpe ratio", f"{a['sharpe']:.2f}", f"{b['sharpe']:.2f}",
                  b["sharpe"] > a["sharpe"]),
                 ("Annual return", pct(a["cagr"]), pct(b["cagr"]), b["cagr"] > a["cagr"])]
        cells = "".join(
            f'<div class="cmp"><div class="cmp-name">{n}</div>'
            f'<div class="cmp-vals"><span class="was">{x}</span><span class="arrow">to</span>'
            f'<span class="now s1t">{y}</span></div>'
            f'<div class="cmp-tag {"up" if good else "down"}">{"better" if good else "worse"} '
            f'with rules</div></div>' for n, x, y, good in items)
        strip = f'<section class="strip">{cells}</section>'

    legend = "".join(f'<span class="leg"><span class="sw s{k}bg"></span>{esc(label(r))}</span>'
                     for k, r in enumerate(runs))
    has_trips = any(e[1] == "DRAWDOWN_BREAKER" for r in runs for e in r["risk_events"])
    trip_leg = ('<span class="leg"><span class="sw tripbg"></span>Circuit breaker fired, '
                'shaded until trading resumed</span>' if has_trips else "")

    css_series = "".join(f".s{k}{{stroke:{c[0]}}}.s{k}f{{fill:{c[0]}}}.s{k}bg{{background:{c[0]}}}"
                         f".s{k}t{{color:{c[0]}}}" for k, c in enumerate(SERIES))
    css_series_dark = "".join(f".s{k}{{stroke:{c[1]}}}.s{k}f{{fill:{c[1]}}}.s{k}bg{{background:{c[1]}}}"
                              f".s{k}t{{color:{c[1]}}}" for k, c in enumerate(SERIES))
    manifest_rows = "".join(
        f'<tr><td><span class="sw s{k}bg"></span>{esc(short_label(r))}</td>'
        f'<td><code>{r["run_id"]}</code></td>'
        f'<td><code>{r["manifest"]["dataset_sha256"][:16]}</code></td>'
        f'<td><code>{r["manifest"]["engine_version"]}</code></td>'
        f'<td><code>{r["manifest"]["result_fingerprint"][:16]}</code></td></tr>'
        for k, r in enumerate(runs))
    data_json = json.dumps({"dd": dd_data, "eq": eq_data, "ex": ex_data,
                            "labels": [short_label(r) for r in runs]})
    sym = esc(c0["symbol"])
    period = f"{month_year(s0['start'])} to {month_year(s0['end'])}"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RiskLens report: {sym}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{color-scheme:light;--bg:#fbfcfd;--panel:#ffffff;--ink:#14213d;--ink2:#4a5568;--ink3:#7b8796;
--rule:#dde3ea;--grid:#e8edf2;--halt:rgba(199,119,0,.10);--trip:#c77700;--good:#0f7b4f;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#0f151c;--panel:#151d26;--ink:#e6ebf0;
--ink2:#b3bdc8;--ink3:#7f8b98;--rule:#26313c;--grid:#1f2933;--halt:rgba(240,170,60,.12);--trip:#f0aa3c;
--good:#4cc38a;--bad:#ff8a80}}}}
{css_series}
@media (prefers-color-scheme:dark){{{css_series_dark}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}}
main{{max-width:1040px;margin:0 auto;padding:40px 24px 64px}}
.product{{font-weight:600;font-size:15px;color:var(--ink2);margin:0 0 28px;display:flex;gap:10px;align-items:center}}
.product i{{display:inline-block;width:18px;height:18px;border-radius:50%;border:3px solid {SERIES[1][0]};}}
.context{{color:var(--ink2);margin:0 0 6px;font-size:15px}}
h1{{font-size:32px;line-height:1.25;font-weight:500;margin:0 0 28px;max-width:30ch;letter-spacing:-.01em}}
h2{{font-size:19px;font-weight:600;margin:48px 0 6px}}
.sub{{color:var(--ink2);margin:0 0 14px;font-size:15px;max-width:70ch}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 22px;font-size:14px;color:var(--ink2);margin:0 0 10px}}
.leg{{display:inline-flex;align-items:center}}
.sw{{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:8px;flex:none;vertical-align:-1px}}
.tripbg{{background:var(--halt);border:2px solid var(--trip)}}
.figure{{position:relative;background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:14px 12px 6px}}
.chart{{display:block;width:100%;height:auto;overflow:visible}}
.grid{{stroke:var(--grid);stroke-width:1}} .grid.zero{{stroke:var(--ink3)}}
.ytick{{stroke:var(--ink3)}}
.tick{{fill:var(--ink3);font-size:12px}}
.line{{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
.fill{{opacity:.16;stroke:none}}
.halt{{fill:var(--halt)}} .trip{{fill:var(--trip)}}
.xhair{{stroke:var(--ink3);stroke-width:1;stroke-dasharray:3 3}}
.hit{{fill:transparent;cursor:crosshair}}
.tip{{position:absolute;top:10px;pointer-events:none;background:var(--panel);border:1px solid var(--rule);
border-radius:8px;padding:8px 10px;font-size:13px;display:none;box-shadow:0 4px 14px rgba(0,0,0,.08);min-width:150px}}
.tip b{{display:block;margin-bottom:4px;font-weight:600}}
.tip div{{display:flex;align-items:center;justify-content:space-between;gap:12px}}
.strip{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin:22px 0 0;
border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}}
.cmp{{padding:16px 18px 14px 0}} .cmp+.cmp{{padding-left:18px;border-left:1px solid var(--rule)}}
.cmp-name{{font-size:14px;color:var(--ink2)}}
.cmp-vals{{display:flex;align-items:baseline;gap:8px;margin:4px 0 2px;flex-wrap:wrap}}
.was{{color:var(--ink3);font-size:17px;text-decoration:line-through;text-decoration-color:var(--rule)}}
.arrow{{color:var(--ink3);font-size:13px}}
.now{{font-size:26px;font-weight:600;letter-spacing:-.01em}}
.cmp-tag{{font-size:13px}} .cmp-tag.up{{color:var(--good)}} .cmp-tag.down{{color:var(--bad)}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:15px}}
th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid var(--rule);vertical-align:top}}
thead th{{font-weight:600;font-size:14px;color:var(--ink2);border-bottom:2px solid var(--rule)}}
tbody th{{font-weight:400;color:var(--ink2)}}
.num{{text-align:right;white-space:nowrap}}
td.best{{font-weight:600}}
tr.group th{{font-weight:600;color:var(--ink);padding-top:20px;border-bottom:1px solid var(--rule)}}
.metrics td,.metrics thead th.num{{min-width:150px}}
.events td.date{{white-space:nowrap;color:var(--ink2)}}
.luck{{margin:0 0 22px;max-width:760px}} .luck p{{margin:0 0 10px}}
.pbar{{position:relative;height:8px;border-radius:4px;background:var(--grid)}}
.pbar .thr{{position:absolute;top:-4px;width:2px;height:16px;background:var(--ink3)}}
.pbar .dot{{position:absolute;top:-4px;width:16px;height:16px;border-radius:50%;margin-left:-8px;border:2px solid var(--panel)}}
.pscale{{display:flex;justify-content:space-between;font-size:12px;color:var(--ink3);margin-top:6px;position:relative}}
.thrlab{{position:absolute;left:5%;transform:translateX(-50%)}}
.note{{color:var(--ink2);font-size:14px;max-width:70ch}}
details{{margin-top:48px;border-top:1px solid var(--rule);padding-top:14px}}
summary{{cursor:pointer;font-weight:600}}
summary:focus-visible,.hit:focus-visible{{outline:2px solid {SERIES[1][0]};outline-offset:3px}}
code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}}
@media (max-width:720px){{h1{{font-size:24px}} .strip{{grid-template-columns:repeat(2,minmax(0,1fr))}}
.cmp:nth-child(3){{border-left:0;padding-left:0}} .cmp:nth-child(n+3){{border-top:1px solid var(--rule)}}
main{{padding:24px 16px 48px}}}}
</style></head><body><main>
<div class="product"><i></i>RiskLens</div>
<p class="context">{sym}, {period}. {esc(strategy_name(runs[0]))} strategy on ${c0['initial_cash']:,.0f}.</p>
<h1>{esc(verdict(runs))}</h1>

<h2>How far below its peak was the money?</h2>
<p class="sub">Each dip is a drawdown: the percentage lost since the last high point. Deeper and
longer dips are what risk management tries to prevent.</p>
<div class="legend">{legend}{trip_leg}</div>
<div class="figure">{dd_svg}<div class="tip" id="dd-tip"></div></div>
{strip}

<h2>Growth of ${c0['initial_cash']:,.0f}</h2>
<div class="figure">{eq_svg}<div class="tip" id="eq-tip"></div></div>

<h2>How much was invested</h2>
<p class="sub">Share of the money held in {sym} each day. The risk rules hold less when the market is
volatile and step out entirely after a breaker trip.</p>
<div class="figure">{ex_svg}<div class="tip" id="ex-tip"></div></div>

<h2>All metrics</h2>
<p class="sub">Daily data, 252 trading days per year. Orders decided at a close fill at the next
day’s open. Fees {c0['costs']['fee_bps']:g} bp and slippage {c0['costs']['slippage_bps']:g} bp per trade.
Bold marks the better value.</p>
{metrics_table(runs)}

<h2>Luck test: is the timing better than random?</h2>
<p class="sub">We shuffle which days the strategy was invested, keeping how much it was invested,
and re-score it many times. If random timing often does as well, the result could be luck.</p>
{luck_block(runs)}

<h2>Risk-rule log</h2>
{events_block(runs)}

<details><summary>Reproducibility</summary>
<p class="note">Each run ID is a hash of the settings, the exact data file and the engine code.
The same three inputs always give the same ID and the identical result.</p>
<div class="scroll"><table><thead><tr><th>Run</th><th>Run ID</th><th>Data SHA-256</th>
<th>Engine</th><th>Result fingerprint</th></tr></thead><tbody>{manifest_rows}</tbody></table></div>
</details>
</main>
<script>
const D={data_json};
const F={{dd:v=>(v*100).toFixed(1).replace('-','−')+'%',eq:v=>'$'+Math.round(v).toLocaleString(),
ex:v=>Math.round(v*100)+'%'}};
for (const id of ['dd','eq','ex']) {{
  const svg=document.getElementById(id), tip=document.getElementById(id+'-tip'), d=D[id];
  const hit=svg.querySelector('.hit'), xh=svg.querySelector('.xhair'), box=svg.parentElement;
  function show(clientX){{
    const r=svg.getBoundingClientRect(), sx=(clientX-r.left)*d.w/r.width;
    const n=d.dates.length, j=Math.max(0,Math.min(n-1,Math.round((sx-d.pl)/d.pw*(n-1))));
    const x=d.pl+d.pw*j/Math.max(1,n-1);
    xh.setAttribute('x1',x);xh.setAttribute('x2',x);xh.setAttribute('visibility','visible');
    tip.innerHTML='<b>'+d.dates[j]+'</b>'+d.vals.map((v,k)=>'<div><span><span class="sw s'+k+'bg"></span>'+
      D.labels[k]+'</span><span>'+F[id](v[j])+'</span></div>').join('');
    tip.style.display='block';
    const px=x/d.w*r.width+(r.left-box.getBoundingClientRect().left);
    const w=tip.offsetWidth, bw=box.clientWidth;
    tip.style.left=Math.max(0,Math.min(bw-w,(px+14+w>bw? px-14-w : px+14)))+'px';
  }}
  hit.addEventListener('mousemove',e=>show(e.clientX));
  hit.addEventListener('touchmove',e=>{{show(e.touches[0].clientX)}},{{passive:true}});
  hit.addEventListener('mouseleave',()=>{{tip.style.display='none';xh.setAttribute('visibility','hidden')}});
}}
</script></body></html>"""
