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
import re

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


# ------------------------------------------------------------------ icons
# Simple stroke icons (24x24 grid), drawn inline so the report works offline.
_ICON_PATHS = {
    "summary": '<path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 15l3-4 3 2 5-6"/>',
    "shield": '<path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/>',
    "wave": '<path d="M3 12c2-4 4-4 6 0s4 4 6 0 4-4 6 0"/>',
    "gauge": '<path d="M4 17a8 8 0 1 1 16 0"/><path d="M12 17l4-5"/>',
    "up": '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "down": '<path d="M3 7l6 6 4-4 8 8"/><path d="M15 17h6v-6"/>',
    "bolt": '<path d="M13 3L4 14h7l-1 7 9-11h-7z"/>',
    "pie": '<circle cx="12" cy="12" r="9"/><path d="M12 3v9h9"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
    "dice": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h.01M15 9h.01M9 15h.01M15 15h.01"/>',
    "bell": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    "alert": '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "tail": '<path d="M3 20h18"/><path d="M5 20c2-1 3-12 7-12s5 11 7 12"/>',
    "layers": '<path d="M12 4l8 4-8 4-8-4z"/><path d="M4 12l8 4 8-4"/><path d="M4 16l8 4 8-4"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
    "scale": '<path d="M12 4v16M5 20h14"/><path d="M5 8h14"/><path d="M5 8l-3 6a3 3 0 0 0 6 0zM19 8l-3 6a3 3 0 0 0 6 0z"/>',
    "cloud": '<path d="M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1.5A4 4 0 0 1 17 18z"/><path d="M9 21l1-2M13 21l1-2"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    "bulb": '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
}


def icon(name, cls="ic"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICON_PATHS[name]}</svg>')


def badge(good: bool, text_good="Better with rules", text_bad="Worse with rules"):
    return (f'<span class="badge {"good" if good else "bad"}">{icon("check" if good else "alert", "bi")}'
            f'{text_good if good else text_bad}</span>')


def section(sid, ic, title, sub, body, explain=None):
    tip = (f'<div class="explain">{icon("bulb", "ei")}<p>{explain}</p></div>' if explain else "")
    return (f'<section class="card" id="{sid}"><header class="card-h"><span class="ib">{icon(ic)}</span>'
            f'<div><h2>{title}</h2><p class="sub">{sub}</p></div></header>{body}{tip}</section>')


# ---------------------------------------------------------- drawdown table
def top_drawdowns(equity, dates, n=5):
    """Distinct drawdown episodes (peak -> trough -> recovery), deepest first."""
    eps = []
    peak_i = 0
    trough_i = 0
    in_dd = False
    for i, e in enumerate(equity):
        if e >= equity[peak_i]:
            if in_dd:
                eps.append((peak_i, trough_i, i))
                in_dd = False
            peak_i = trough_i = i
        else:
            in_dd = True
            if e < equity[trough_i]:
                trough_i = i
    if in_dd:
        eps.append((peak_i, trough_i, None))
    rows = []
    for p, t, r in eps:
        depth = 1 - equity[t] / equity[p]
        if depth <= 0:
            continue
        rows.append({"depth": depth, "peak": dates[p], "trough": dates[t],
                     "recovered": dates[r] if r is not None else None,
                     "days": (r if r is not None else len(equity) - 1) - p})
    rows.sort(key=lambda x: -x["depth"])
    return rows[:n]


def drawdown_tables(runs):
    out = []
    for k, r in enumerate(runs):
        rows = top_drawdowns(r["series"]["equity"], r["series"]["dates"])
        body = "".join(
            f'<tr><td class="num">{i + 1}</td><td class="num strong">{loss(x["depth"])}</td>'
            f'<td>{x["peak"]}</td><td>{x["trough"]}</td>'
            f'<td>{x["recovered"] or "<span class=muted>not yet</span>"}</td>'
            f'<td class="num">{x["days"]:,}</td></tr>' for i, x in enumerate(rows))
        out.append(f'<div class="ddt"><h3><span class="sw s{k}bg"></span>{esc(short_label(r))}</h3>'
                   f'<div class="scroll"><table><thead><tr><th class="num">#</th><th class="num">Fall</th>'
                   f'<th>Peak</th><th>Bottom</th><th>Recovered</th><th class="num">Trading days</th>'
                   f'</tr></thead><tbody>{body or "<tr><td colspan=6>No drawdowns</td></tr>"}'
                   f'</tbody></table></div></div>')
    return f'<div class="ddgrid">{"".join(out)}</div>'


# ------------------------------------------------------------- stress tests
# Peak and trough dates of the S&P 500 for well-known market sell-offs.
STRESS_EVENTS = [
    ("2008 financial crisis", "2007-10-09", "2009-03-09"),
    ("2011 US credit downgrade", "2011-07-22", "2011-10-03"),
    ("2015–16 China slowdown", "2015-07-20", "2016-02-11"),
    ("Late-2018 sell-off", "2018-09-20", "2018-12-24"),
    ("COVID crash", "2020-02-19", "2020-03-23"),
    ("2022 bear market", "2022-01-03", "2022-10-12"),
    ("2025 tariff shock", "2025-02-19", "2025-04-08"),
]


def _index_on_or_after(dates, d):
    for i, x in enumerate(dates):
        if x >= d:
            return i
    return None


def stress_block(runs):
    dates = runs[0]["series"]["dates"]
    rows = []
    for name, a, b in STRESS_EVENTS:
        if a < dates[0] or b > dates[-1]:
            continue
        i, j = _index_on_or_after(dates, a), _index_on_or_after(dates, b)
        if i is None or j is None or j <= i:
            continue
        close = runs[0]["series"]["close"]
        asset = close[j] / close[i] - 1
        cells = []
        rets = []
        for k, r in enumerate(runs):
            eq = r["series"]["equity"]
            ret = eq[j] / eq[i] - 1
            rets.append(ret)
            cells.append(f'<td class="num strong">{pct(ret, 1, signed=True)}</td>')
        verdict = ""
        if len(runs) == 2 and not has_rules(runs[0]) and has_rules(runs[1]):
            saved = rets[1] - rets[0]
            verdict = (f'<td>{badge(saved >= 0, f"{abs(saved) * 100:.1f} pts less loss", f"{abs(saved) * 100:.1f} pts more loss")}</td>')
        rows.append(f'<tr><th>{name}<div class="muted small">{a} to {b}</div></th>'
                    f'<td class="num">{pct(asset, 1, signed=True)}</td>{"".join(cells)}{verdict}</tr>')
    if not rows:
        return ('<p class="empty">None of the famous sell-offs fall inside this date range. '
                'Download a longer history (for example from 2007) to see them.</p>')
    head = "".join(f'<th class="num"><span class="sw s{k}bg"></span>{esc(short_label(r))}</th>'
                   for k, r in enumerate(runs))
    extra = "<th>Effect of the rules</th>" if len(runs) == 2 and has_rules(runs[1]) and not has_rules(runs[0]) else ""
    note = ""
    if runs[0]["config"]["symbol"] == "SYNTH":
        note = ('<p class="note">SYNTH is generated test data, so these dates are only an '
                'illustration. Use real data (SPY, AAPL) for meaningful stress tests.</p>')
    return (f'<div class="scroll"><table class="stress"><thead><tr><th>Event</th>'
            f'<th class="num">{esc(runs[0]["config"]["symbol"])} itself</th>{head}{extra}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>{note}')


# ---------------------------------------------------------------- metrics
ROWS = [
    ("Return", [
        ("Final value", lambda s: f"${s['final_equity']:,.0f}", "final_equity", +1,
         "What the starting money grew to."),
        ("Total return", lambda s: pct(s["total_return"]), "total_return", +1,
         "Gain over the whole period."),
        ("Annual return (CAGR)", lambda s: pct(s["cagr"]), "cagr", +1,
         "Average yearly growth rate."),
    ]),
    ("Risk", [
        ("Max drawdown", lambda s: loss(s["max_drawdown"]), "max_drawdown", -1,
         "Biggest fall from a high point. The pain an investor would have lived through."),
        ("Days to recover", lambda s: "not recovered" if s["drawdown_recovery_days"] is None
         and s["max_drawdown"] > 0 else f"{s['drawdown_recovery_days']}", None, 0,
         "Trading days from the bottom back to the old high."),
        ("Volatility (annual)", lambda s: pct(s["volatility"]), "volatility", -1,
         "How bumpy the ride is. Typical yearly swing."),
        ("1-day VaR, 95%", lambda s: loss(s["var_95"], 2), "var_95", -1,
         "On the worst 1 in 20 days, you lose at least this much."),
        ("1-day Expected Shortfall", lambda s: loss(s["cvar_95"], 2), "cvar_95", -1,
         "Average loss on those worst 1 in 20 days."),
        ("Worst single day", lambda s: loss(s["worst_day"]), "worst_day", -1,
         "The single biggest one-day drop."),
    ]),
    ("Return per unit of risk", [
        ("Sharpe ratio", lambda s: f"{s['sharpe']:.2f}", "sharpe", +1,
         "Return divided by volatility. Higher is better; above 1 is very good."),
        ("Sortino ratio", lambda s: f"{s['sortino']:.2f}", "sortino", +1,
         "Like Sharpe, but only counts downside swings as risk."),
    ]),
    ("Trading activity", [
        ("Time in the market", lambda s: pct(s["time_in_market"], 0), None, 0,
         "Share of days holding the stock."),
        ("Trades", lambda s: f"{s['trades']}", None, 0, "Number of buy and sell orders."),
        ("Fees paid", lambda s: f"${s['fees_paid']:,.0f}", None, 0,
         "Trading fees, included in all results above."),
    ]),
]


def metrics_table(runs) -> str:
    head = "".join(f'<th class="num"><span class="sw s{k}bg"></span>{esc(short_label(r))}</th>'
                   for k, r in enumerate(runs))
    body = []
    for group, rows in ROWS:
        body.append(f'<tr class="group"><th colspan="{len(runs) + 2}">{group}</th></tr>')
        for name, f, key, better, meaning in rows:
            vals = [r["summary"][key] for r in runs] if key else None
            best = None
            if key and better and len(runs) > 1 and len(set(vals)) > 1:
                best = vals.index(max(vals) if better > 0 else min(vals))
            cells = "".join(
                f'<td class="num{" best" if k == best else ""}">{esc(f(r["summary"]))}</td>'
                for k, r in enumerate(runs))
            body.append(f'<tr><th>{name}</th>{cells}<td class="meaning">{meaning}</td></tr>')
    return (f'<div class="scroll"><table class="metrics"><thead><tr><th>Metric</th>{head}'
            f'<th>What it means</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def luck_block(runs) -> str:
    out = []
    for k, r in enumerate(runs):
        lt = r["luck_test"]
        if not lt:
            continue
        p = lt["p_value"]
        real = p < 0.05
        msg = (f"Only {p:.1%} of random timings did as well, so the timing likely carries real "
               f"information." if real else
               f"{p:.0%} of random timings did at least as well, so the result is consistent with luck.")
        out.append(
            f'<div class="luck"><div class="luck-h"><span class="sw s{k}bg"></span>'
            f'<strong>{esc(short_label(r))}</strong>'
            f'{badge(real, "Timing looks real", "Could be luck")}</div>'
            f'<p>Sharpe {lt["actual_sharpe_gross"]:.2f} before costs, against a median of '
            f'{lt["random_median_sharpe"]:.2f} for {lt["trials"]:,} random timings. {msg}</p>'
            f'<div class="pbar" aria-label="p-value {p:.3f}"><span class="zone"></span>'
            f'<span class="dot s{k}bg" style="left:{min(99.5, p * 100):.1f}%"></span></div>'
            f'<div class="pscale"><span>0</span><span class="thrlab">0.05</span>'
            f'<span class="pv">p = {p:.3f}</span><span>1</span></div></div>')
    return "".join(out) or '<p class="empty">The luck test was not run.</p>'


def fmt_p(p):
    return "< 0.001" if p < 0.001 else f"{p:.3f}" if p < 0.1 else f"{p:.2f}"


VERDICT = {"accurate": (True, "Accurate"), "too optimistic": (False, "Underestimates risk"),
           "too cautious": (False, "Overestimates risk")}


def _risk_chart(series, key, cid) -> str:
    """Daily returns (bars), one model's VaR forecast made the day before
    (line), and the invested days when the loss beat it (red dots)."""
    rets, var, dates, inv = series["ret"], series["var"][key], series["dates"], series["invested"]
    n, height = len(rets), 250
    lo = min(min(rets), -max(var))
    hi = max(rets)
    ticks = _nice_ticks(lo, hi)
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    pw, ph = VW - PL - PR, height - PT - PB
    X = lambda i: PL + pw * i / max(1, n - 1)
    Y = lambda v: PT + ph * (1 - (v - lo) / (hi - lo or 1))
    g = [f'<svg class="chart" id="{cid}" viewBox="0 0 {VW} {height}" role="img" aria-label="Daily returns '
         f'against the VaR forecast, with breaches marked">']
    for t in ticks:
        y = Y(t)
        g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{PL}" x2="{VW - PR}" y1="{y:.1f}" '
                 f'y2="{y:.1f}"/><text class="tick" x="{PL - 8}" y="{y + 4:.1f}" text-anchor="end">'
                 f'{"0%" if abs(t) < 1e-12 else pct(t, 0 if abs(hi - lo) > 0.04 else 1)}</text>')
    last, step = None, max(1, round((int(dates[-1][:4]) - int(dates[0][:4]) + 1) / 8))
    for i, d in enumerate(dates):
        if d[:4] != last:
            if last is not None and int(d[:4]) % step == 0:
                g.append(f'<text class="tick" x="{X(i):.1f}" y="{height - 6}" text-anchor="middle">{d[:4]}</text>')
            last = d[:4]
    y0 = Y(0)
    g.append('<path class="vbar" d="' + "".join(f"M{X(i):.1f} {y0:.1f}V{Y(r):.1f}"
                                                for i, r in enumerate(rets) if r) + '"/>')
    step_i = max(1, n // 900)
    pts = " ".join(f"{X(i):.1f},{Y(-var[i]):.1f}" for i in range(0, n, step_i))
    g.append(f'<polyline class="line varl" points="{pts}"/>')
    g.append("".join(f'<circle class="vx" cx="{X(i):.1f}" cy="{Y(r):.1f}" r="2.6"/>'
                     for i, (r, v) in enumerate(zip(rets, var)) if inv[i] and -r > v))
    g.append("</svg>")
    return "".join(g)


def _chip(ok, yes="Pass", no="Fail"):
    return (f'<span class="badge {"good" if ok else "bad"}">{icon("check" if ok else "alert", "bi")}'
            f'{yes if ok else no}</span>')


def var_block(runs) -> str:
    """Which way of measuring risk is right? Every model is graded by the
    tests a bank must run, on the days the strategy actually held stock."""
    k, main = len(runs) - 1, runs[-1]
    rm = main.get("risk_models") or {}
    c95, c99 = rm.get("95"), rm.get("99")
    if not c95:
        return ('<p class="empty">Not enough history to check the risk models: it needs at least '
                '300 trading days (250 to make the first forecast, then days to test it), with the '
                'strategy holding stock on at least 50 of them.</p>')
    m95 = c95["models"]
    pnl, best_k = m95["pnl_hist"], c95["best"]
    best = m95[best_k]
    days = c95["test_days"]
    in_cash = c95["total_days"] - days
    head = (f'On the {days:,} days the strategy held stock, VaR from the account’s own history was '
            f'beaten <b>{pnl["exceptions"]}</b> times when {pnl["expected"]:.0f} were expected'
            + (", and the misses came in clusters" if pnl["clustered"] else "") + ".")
    if best_k != "pnl_hist":
        head += (f' The best-fitting model, <b>{esc(best["label"])}</b>, was beaten <b>{best["exceptions"]}</b> '
                 f'times' + ("" if best["clustered"] else ", with no clustering") + ".")
    notes = []
    if in_cash > 50 and not pnl["passes"] and m95["pos_hist"]["passes"]:
        notes.append(
            f'<b>Why the account’s own history fails:</b> on the {in_cash:,} days in cash the account '
            f'earns nothing, so a VaR built from its past P&amp;L shrinks toward zero, and the day the '
            f'strategy buys back in, that VaR is far too small. A risk desk sizes risk from what it holds '
            f'today: <b>exposure × the stock’s own risk</b>. Tested on the same days, that passes.')
    if pnl["clustered"] and not m95["pos_ewma"]["clustered"]:
        notes.append('<b>Clustering</b> means several breaches in a row: the model reacted too slowly '
                     'when markets turned wild. EWMA volatility weights recent days more, so it adapts '
                     'within days.')
    if c99:
        m99 = c99["models"]
        if m99["pos_normal"]["clustered"] and not m99[c99["best"]]["clustered"]:
            notes.append('<b>Fat tails:</b> at 99%, the normal (bell-curve) model’s breaches cluster, '
                         'because real crashes are more extreme than a bell curve allows. Models that use '
                         'the stock’s actual history of shocks pass.')
    note_html = "".join(f'<p class="note">{x}</p>' for x in notes)

    series = c95.get("series")
    charts = ""
    if series:
        keys = list(series["var"])
        btns = "".join(f'<button type="button" class="vt{" on" if i == len(keys) - 1 else ""}" '
                       f'data-show="vc-{key}">{esc(m95[key]["label"])}</button>'
                       for i, key in enumerate(keys))
        figs = "".join(f'<div class="figure vfig{" on" if i == len(keys) - 1 else ""}" id="vc-{key}">'
                       f'{_risk_chart(series, key, "vcs-" + key)}</div>' for i, key in enumerate(keys))
        charts = (f'<div class="vtabs" role="group" aria-label="Model shown">{btns}</div>'
                  f'<div class="legend"><span class="leg"><span class="sw vbarbg"></span>Daily return</span>'
                  f'<span class="leg"><span class="sw s1bg"></span>95% VaR forecast, made the day before</span>'
                  f'<span class="leg"><span class="sw vxbg"></span>Loss beat the forecast</span></div>{figs}')

    rows = []
    for key, v in m95.items():
        w = (c99 or {}).get("models", {}).get(key)
        b = (w or {}).get("basel")
        tag = ' <span class="tag">best fit</span>' if key == best_k else ""
        zone = f'<span class="zone {b["zone"]}">{b["zone"].title()}</span>' if b else "–"
        rows.append(
            f'<tr class="{"bestrow" if key == best_k else ""}"><th>{esc(v["label"])}{tag}</th>'
            f'<td class="num">{v["exceptions"]} <span class="muted small">/ {v["expected"]:.0f}</span></td>'
            f'<td class="num">{fmt_p(v["kupiec_p"])}</td><td class="num">{fmt_p(v["christoffersen_p"])}</td>'
            f'<td>{_chip(v["passes"])}</td>'
            + (f'<td class="num">{w["exceptions"]} <span class="muted small">/ {w["expected"]:.0f}</span></td>'
               f'<td>{_chip(w["passes"])}</td><td>{zone}</td>' if w else '<td>–</td><td>–</td><td>–</td>')
            + f'<td class="num">{pct(v["avg_var"], 2)}</td></tr>')
    table = (f'<h3 class="h3">Every model, graded</h3><div class="scroll"><table class="vart"><thead>'
             f'<tr><th rowspan="2">Model</th><th colspan="4" class="grp">95% VaR</th>'
             f'<th colspan="3" class="grp">99% VaR</th><th rowspan="2" class="num">Average<br>95% VaR</th></tr>'
             f'<tr><th class="num">Breaches / expected</th><th class="num">Count test p</th>'
             f'<th class="num">Clustering test p</th><th>Verdict</th><th class="num">Breaches / expected</th>'
             f'<th>Verdict</th><th>Basel light</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
             f'<p class="note">Tested on the {days:,} days the strategy held stock'
             + (f' (out of {c95["total_days"]:,}; a day in cash can’t lose money, so counting it would flatter every model)'
                if in_cash else "")
             + '. A model passes when both the count test (Kupiec) and the clustering test (Christoffersen) '
               'have p ≥ 0.05; the best fit passes both with the highest combined p-value. '
               f'Shown for: <span class="sw s{k}bg"></span>{esc(short_label(main))}.</p>')
    return f'<p class="verdict small-v">{head}</p>{note_html}{charts}{table}'



# =============================================================== deep dive
SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def years_text(y):
    if y == math.inf or y > 1e300:
        return "never, in practice"
    if y >= 1e6:
        e = int(math.floor(math.log10(y)))
        return f"{y / 10 ** e:.1f} × 10{str(e).translate(SUP)} years"
    return f"{y:,.0f} years"


def _deep(run, key):
    return (run.get("deep") or {}).get(key)


def _ratio_text(r):
    return "–" if r is None else f"{r:,.0f}×" if r >= 10 else f"{r:.1f}×"


def _hist_chart(h) -> str:
    """Daily returns in half-sigma bins (log scale), against a bell curve."""
    counts, expected = h["counts"], h["expected"]
    n = len(counts)
    height, pl, pr, pt, pb = 240, 50, 12, 12, 34
    pw, ph = VW - pl - pr, height - pt - pb
    top = math.log10(max(max(counts), max(expected)) + 1)
    top = math.ceil(top)
    Y = lambda c: pt + ph * (1 - math.log10(c + 1) / top)
    bw = pw / n
    g = [f'<svg class="chart" viewBox="0 0 {VW} {height}" role="img" aria-label="Histogram of daily '
         f'returns against a bell curve, log scale">']
    for e in range(0, top + 1):
        y = Y(10 ** e - 1)
        g.append(f'<line class="grid" x1="{pl}" x2="{VW - pr}" y1="{y:.1f}" y2="{y:.1f}"/>'
                 f'<text class="tick" x="{pl - 8}" y="{y + 4:.1f}" text-anchor="end">{10 ** e - 1 if e == 0 else f"{10 ** e:,}"}</text>')
    for j, c in enumerate(counts):
        x = pl + j * bw
        far = j <= 4 or j >= n - 5
        hot = far and c >= 1 and c > 2 * expected[j]
        if c:
            g.append(f'<rect class="hbar{" hot" if hot else ""}" x="{x + 1:.1f}" y="{Y(c):.1f}" '
                     f'width="{bw - 2:.1f}" height="{pt + ph - Y(c):.1f}"><title>{c} days</title></rect>')
    pts = " ".join(f"{pl + (j + .5) * bw:.1f},{Y(e):.1f}" for j, e in enumerate(expected))
    g.append(f'<polyline class="line normc" points="{pts}"/>')
    for j in range(0, n, 2):
        lab = "≤ −6σ" if j == 0 else "≥ 6σ" if j == n - 1 else f"{(j - 12) / 2:+g}σ".replace("+0σ", "0").replace("-", "−")
        if j in (0, n - 1) or (j - 12) % 4 == 0:
            g.append(f'<text class="tick" x="{pl + (j + .5) * bw:.1f}" y="{height - 12}" text-anchor="middle">{lab}</text>')
    g.append("</svg>")
    return "".join(g)


def tails_block(runs) -> str:
    t = _deep(runs[-1], "tails")
    if not t:
        return '<p class="empty">Not enough data to study the tails (needs 60 days).</p>'
    sym = esc(runs[0]["config"]["symbol"])
    b = {x["sigma"]: x for x in t["beyond"]}
    w = t["worst"]
    head = (f'{sym}’s big losses are far more common than a bell curve predicts: days worse than 3 standard '
            f'deviations happened <b>{b[3]["observed"]}</b> times, where a normal distribution expects '
            f'{b[3]["expected"]:.1f}' + (f' ({_ratio_text(b[3]["ratio"])})' if b[3]["ratio"] and b[3]["ratio"] >= 1.5 else "")
            + (f'; beyond 4, <b>{b[4]["observed"]}</b> vs {b[4]["expected"]:.2f}' if b[4]["observed"] else "") + ".")
    uni = " That is longer than the age of the universe." if w["normal_years"] > 1.38e10 else ""
    notes = [f'<b>The worst day</b>, {esc(w["date"])} ({pct(w["return"])}), was a {abs(w["sigma"]):.1f}-sigma move. '
             f'A bell curve says that should happen about once every {years_text(w["normal_years"])}.{uni}',
             f'<b>Kurtosis</b> is {t["kurtosis"]:.1f}, where a bell curve has 3: more quiet days and more extreme '
             f'days than “normal”.' + (f' <b>Skew</b> is {t["skew"]:.2f}: the big moves lean to the downside.'
                                         if t["skew"] < -0.2 else "")]
    if t["var99_hist"] > t["var99_normal"] * 1.02:
        notes.append(f'<b>Why it matters:</b> a VaR that assumes a bell curve puts the 1-in-100 daily loss at '
                     f'{pct(t["var99_normal"], 2)}; the real history says {pct(t["var99_hist"], 2)}. Models that use '
                     f'the actual history of shocks (see the risk model check) don’t make that mistake.')
    if t["tail_ratio"] and t["tail_ratio"] > 1.1:
        notes.append(f'The worst 1% of days averaged {pct(t["avg_worst_1pct"], 2)}, the best 1% '
                     f'{pct(t["avg_best_1pct"], 2, signed=True)}: crashes are {t["tail_ratio"]:.1f}× bigger than rallies.')
    rows = "".join(f'<tr><th>Worse than −{x["sigma"]}σ</th><td class="num"><b>{x["observed"]}</b></td>'
                   f'<td class="num">{x["expected"]:.2f}</td><td class="num">{_ratio_text(x["ratio"])}</td></tr>'
                   for x in t["beyond"])
    return (f'<p class="verdict small-v">{head}</p>' + "".join(f'<p class="note">{x}</p>' for x in notes) +
            f'<div class="legend"><span class="leg"><span class="sw hbarbg"></span>Days in each band</span>'
            f'<span class="leg"><span class="sw hotbg"></span>Far tails, more than twice the bell curve</span>'
            f'<span class="leg"><span class="sw normbg"></span>What a bell curve expects</span></div>'
            f'<div class="figure">{_hist_chart(t["hist"])}</div>'
            f'<h3 class="h3">Big losses: real vs bell curve ({t["days"]:,} days of {sym})</h3>'
            f'<div class="scroll"><table><thead><tr><th>Daily loss</th><th class="num">Days it happened</th>'
            f'<th class="num">Bell curve expects</th><th class="num">How much more often</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def _hbar_rows(items, fmt, worst=None):
    """Horizontal bars as an HTML list: (label, value, cls)."""
    top = max((v for _, v, _ in items), default=0) or 1
    return '<div class="hbars">' + "".join(
        f'<div class="hb {c}"><span class="hl">{esc(l)}</span><span class="ht"><i style="width:{max(1.5, v / top * 100):.1f}%">'
        f'</i></span><span class="hv">{fmt(v)}</span></div>' for l, v, c in items) + '</div>'


def rules_block(runs) -> str:
    a = _deep(runs[-1], "attribution")
    if not a:
        return ('<p class="empty">Turn on at least one risk rule (volatility target, circuit breaker or '
                'exposure cap) to see what each one contributes.</p>')
    v = {x["key"]: x for x in a["variants"]}
    none, allr = v["none"], v["all"]
    big = v.get(a["biggest"]) if a["biggest"] else None
    head = ""
    if big and big["dd_cut"] > 0.005:
        head = (f'The <b>{esc(big["label"].replace(" only", "").lower())}</b> did most of the work: alone it cut the '
                f'worst fall from {loss(none["max_drawdown"])} to <b>{loss(big["max_drawdown"])}</b>. ')
    head += f'All rules together: <b>{loss(allr["max_drawdown"])}</b>.'
    notes = []
    br = v.get("breaker")
    if br and br["dd_cut"] < -0.005:
        notes.append(f'<b>The circuit breaker alone made the worst fall deeper</b> ({loss(br["max_drawdown"])} vs '
                     f'{loss(none["max_drawdown"])}): it sells after a big drop, sits out the cooldown, and can miss '
                     f'the rebound. It works as a last line of defence, not as the main risk control.')
    elif br and br["dd_cut"] > 0.005:
        notes.append(f'The circuit breaker alone cut the worst fall to {loss(br["max_drawdown"])}, firing '
                     f'{br["breaker_trips"]} time{"s" if br["breaker_trips"] != 1 else ""}.')
    vt = v.get("vol")
    if vt:
        notes.append(f'The volatility target works <i>before</i> losses happen: it shrinks the position when '
                     f'markets get wild, cutting volatility from {pct(none["volatility"])} to {pct(vt["volatility"])}.')
    if a["interaction"] is not None and abs(a["interaction"]) > 0.01:
        notes.append('Together the rules protected <b>more</b> than the sum of each alone.' if a["interaction"] > 0 else
                     'The rules overlap: together they protected <b>less</b> than the sum of each alone.')
    if allr["cagr"] < none["cagr"]:
        notes.append(f'The cost: annual return {pct(none["cagr"])} → {pct(allr["cagr"])}. Sharpe ratio (return per '
                     f'unit of risk) {none["sharpe"]:.2f} → {allr["sharpe"]:.2f}.')
    items = [(x["label"], x["max_drawdown"], "base" if x["key"] == "none" else "all" if x["key"] == "all" else "")
             for x in a["variants"]]
    rows = "".join(
        f'<tr class="{"bestrow" if x["key"] == "all" else ""}"><th>{esc(x["label"])}</th>'
        f'<td class="num">{loss(x["max_drawdown"])}</td><td class="num">{pct(x["volatility"])}</td>'
        f'<td class="num">{pct(x["cagr"])}</td><td class="num">{x["sharpe"]:.2f}</td>'
        f'<td class="num">{loss(x["var_95"], 2)}</td><td class="num">{loss(x["worst_day"], 2)}</td>'
        f'<td class="num">{x["breaker_trips"]}</td><td class="num">{pct(x["time_in_market"], 0)}</td></tr>'
        for x in a["variants"])
    return (f'<p class="verdict small-v">{head}</p>' + "".join(f'<p class="note">{x}</p>' for x in notes) +
            f'<h3 class="h3">Worst fall with each rule on its own</h3>{_hbar_rows(items, loss)}'
            f'<h3 class="h3">Every number, each rule alone</h3><div class="scroll"><table><thead><tr><th>Rules on</th>'
            f'<th class="num">Max drawdown</th><th class="num">Volatility</th><th class="num">Annual return</th>'
            f'<th class="num">Sharpe</th><th class="num">1-day VaR 95%</th><th class="num">Worst day</th>'
            f'<th class="num">Breaker trips</th><th class="num">Time invested</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _heat(r, metric, fmt, good_high):
    axes, grid = r["axes"], r["grid"]
    fs, ss = axes["fast"], axes["slow"]
    vals = [g[metric] for g in grid]
    lo, hi = min(vals), max(vals)
    cell = {(g["params"]["fast"], g["params"]["slow"]): g[metric] for g in grid}
    chosen = (r["chosen"]["fast"], r["chosen"]["slow"])
    out = ['<table class="heat"><thead><tr><th>fast ↓ / slow →</th>' + "".join(f"<th>{x}</th>" for x in ss) + "</tr></thead><tbody>"]
    for f in fs:
        out.append(f"<tr><th>{f}</th>")
        for sl in ss:
            v = cell.get((f, sl))
            if v is None:
                out.append('<td class="na">–</td>')
                continue
            q = (v - lo) / (hi - lo) if hi > lo else 0.5
            q = q if good_high else 1 - q
            out.append(f'<td class="{"me" if (f, sl) == chosen else ""}" style="--q:{q:.2f}">{fmt(v)}</td>')
        out.append("</tr>")
    return "".join(out) + "</tbody></table>"


def robust_block(runs) -> str:
    r = _deep(runs[-1], "robustness")
    if not r:
        return '<p class="empty">Not available for this run.</p>'
    bh = r["buy_and_hold"]
    parts = []
    head = ""
    if r.get("n"):
        n, rank = r["n"], r["chosen_rank"]
        head = (f'<b>{r["beat_bh_drawdown"]} of {n}</b> nearby settings also had a smaller worst fall than buy & hold '
                f'({loss(bh["max_drawdown"])}), and <b>{r["beat_bh_sharpe"]} of {n}</b> a higher Sharpe ratio '
                f'({bh["sharpe"]:.2f}). Your setting ranks <b>{rank} of {n}</b> by Sharpe. ')
        if rank <= max(1, n // 5) and r["sharpe_median"] < 0.75 * r["sharpe_max"]:
            head += ('It is near the top of its neighbours, so part of its result may be the luck of picking these '
                     'exact numbers: judge it by the typical neighbour, not the best.')
        else:
            head += 'The result does not depend on picking exactly these numbers.'
        r = dict(r, chosen=runs[-1]["config"]["strategy"]["params"])
        if r["strategy"] == "sma_cross":
            parts.append('<h3 class="h3">Sharpe ratio for nearby settings (yours outlined)</h3>'
                         f'<div class="scroll">{_heat(r, "sharpe", lambda v: f"{v:.2f}", True)}</div>'
                         '<h3 class="h3">Worst fall for nearby settings</h3>'
                         f'<div class="scroll">{_heat(r, "max_drawdown", lambda v: loss(v, 0), False)}</div>')
        else:
            items = [(f'{g["params"]["lookback"]} days' + (" (yours)" if g["params"] == r["chosen"] else ""),
                      g["sharpe"], "all" if g["params"] == r["chosen"] else "") for g in r["grid"]]
            parts.append('<h3 class="h3">Sharpe ratio by look-back</h3>' +
                         _hbar_rows([(l, max(v, 0.0), c) for l, v, c in items], lambda v: f"{v:.2f}"))
    else:
        head = "Buy & hold has no settings to tune, so there is nothing to overfit. "
    h, hb = r.get("halves"), r.get("halves_bh")
    if h:
        s1, s2 = h[0]["sharpe"], h[1]["sharpe"]
        same = (s1 > 0) == (s2 > 0) and min(abs(s1), abs(s2)) >= 0.5 * max(abs(s1), abs(s2))
        head += (f' Split in two, it <b>{"held up" if same else "did not hold up"}</b>: Sharpe {s1:.2f} in the first '
                 f'half, {s2:.2f} in the second.')
        rows = "".join(
            f'<tr><th>{esc(x["from"][:7])} to {esc(x["to"][:7])}</th><td class="num">{x["sharpe"]:.2f}</td>'
            f'<td class="num">{loss(x["max_drawdown"])}</td><td class="num">{pct(x["cagr"])}</td>'
            f'<td class="num">{y["sharpe"]:.2f}</td><td class="num">{loss(y["max_drawdown"])}</td></tr>'
            for x, y in zip(h, hb))
        parts.append('<h3 class="h3">First half vs second half</h3><div class="scroll"><table><thead><tr><th>Period</th>'
                     '<th class="num">Sharpe</th><th class="num">Max drawdown</th><th class="num">Annual return</th>'
                     '<th class="num">Buy &amp; hold Sharpe</th><th class="num">Buy &amp; hold drawdown</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>')
    return f'<p class="verdict small-v">{head}</p>' + "".join(parts)


def _scatter(xs, ys, beta, alpha) -> str:
    height, pl, pr, pt, pb = 300, 58, 12, 12, 30
    pw, ph = VW - pl - pr, height - pt - pb
    m = max(max(abs(v) for v in xs), max(abs(v) for v in ys)) or 0.01
    pos = _nice_ticks(0, m)
    step = pos[1] - pos[0] if len(pos) > 1 else m
    while pos[-1] < m - 1e-12:
        pos.append(round(pos[-1] + step, 10))
    lim = [-x for x in reversed(pos[1:])] + pos
    lo, hi = lim[0], lim[-1]
    X = lambda v: pl + pw * (v - lo) / (hi - lo)
    Y = lambda v: pt + ph * (1 - (v - lo) / (hi - lo))
    g = [f'<svg class="chart" viewBox="0 0 {VW} {height}" role="img" aria-label="Strategy daily return against '
         f'the stock&#39;s, with the fitted line">']
    for t in lim:
        g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{pl}" x2="{VW - pr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                 f'<text class="tick" x="{pl - 8}" y="{Y(t) + 4:.1f}" text-anchor="end">{pct(t, 0)}</text>'
                 f'<text class="tick" x="{X(t):.1f}" y="{height - 8}" text-anchor="middle">{pct(t, 0)}</text>')
    step = max(1, len(xs) // 2000)
    g.append("".join(f'<circle class="sc" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="1.6"/>'
                     for x, y in list(zip(xs, ys))[::step]))
    g.append(f'<line class="fit" x1="{X(lo):.1f}" y1="{Y(alpha + beta * lo):.1f}" x2="{X(hi):.1f}" '
             f'y2="{Y(alpha + beta * hi):.1f}"/></svg>')
    return "".join(g)


def sources_block(runs) -> str:
    main = runs[-1]
    so = _deep(main, "sources")
    if not so:
        return '<p class="empty">Not enough data (needs 60 days).</p>'
    sym = esc(main["config"]["symbol"])
    sig = abs(so["alpha_t"]) >= 2
    head = (f'Beta <b>{so["beta"]:.2f}</b>: on an average day the strategy moves {so["beta"]:.2f}× as much as {sym}, '
            f'and {so["r2"]:.0%} of its daily ups and downs are explained by the stock alone. What is left over (alpha) '
            f'is <b>{pct(so["alpha_annual"], 1, signed=True)} a year</b> (t = {so["alpha_t"]:.1f}): '
            + ("statistically significant at the 5% level." if sig else
               "statistically indistinguishable from zero, so no evidence of skill beyond how much stock it held."))
    notes = []
    if so["up_capture"] is not None and so["down_capture"] is not None:
        notes.append(f'It captured <b>{so["up_capture"]:.0%}</b> of {sym}’s average up day and <b>{so["down_capture"]:.0%}</b> '
                     f'of its average down day' + (", so it gave up less on bad days than it missed on good ones."
                                                     if so["down_capture"] < so["up_capture"] - 0.02 else
                                                     ", about the same: it mostly just holds less stock." if abs(so["down_capture"] - so["up_capture"]) <= 0.02
                                                     else ", so it took more of the bad days than the good ones."))
    if so["avg_exposure"] is not None:
        notes.append(f'On average {so["avg_exposure"]:.0%} of the account was in the stock.')
    rows = "".join(
        f'<tr><th><span class="sw s{k}bg"></span>{esc(short_label(r))}</th>'
        f'<td class="num">{x["beta"]:.2f}</td><td class="num">{pct(x["alpha_annual"], 1, signed=True)}</td>'
        f'<td class="num">{x["alpha_t"]:.1f}</td><td class="num">{x["r2"]:.0%}</td>'
        f'<td class="num">{"–" if x["up_capture"] is None else format(x["up_capture"], ".0%")}</td>'
        f'<td class="num">{"–" if x["down_capture"] is None else format(x["down_capture"], ".0%")}</td>'
        f'<td class="num">{"–" if x["avg_exposure"] is None else format(x["avg_exposure"], ".0%")}</td></tr>'
        for k, r in enumerate(runs) for x in [_deep(r, "sources")] if x)
    s = main["series"]
    asset = [s["close"][i + 1] / s["close"][i] - 1 for i in range(len(s["close"]) - 1)]
    strat = [s["equity"][i + 1] / s["equity"][i] - 1 for i in range(len(s["equity"]) - 1)]
    alpha_d = so["alpha_annual"] / 252
    return (f'<p class="verdict small-v">{head}</p>' + "".join(f'<p class="note">{x}</p>' for x in notes) +
            f'<h3 class="h3">Every day: the strategy vs {sym}</h3><div class="legend"><span class="leg"><span class="sw scbg"></span>'
            f'One day</span><span class="leg"><span class="sw s1bg"></span>Fitted line: slope = beta</span></div>'
            f'<div class="figure">{_scatter(asset, strat, so["beta"], alpha_d)}</div>'
            f'<div class="scroll"><table><thead><tr><th>Run</th><th class="num">Beta</th><th class="num">Alpha / year</th>'
            f'<th class="num">Alpha t-stat</th><th class="num">Explained by the stock (R²)</th><th class="num">Up capture</th>'
            f'<th class="num">Down capture</th><th class="num">Average invested</th></tr></thead><tbody>{rows}</tbody></table></div>')


def regimes_block(runs) -> str:
    main = runs[-1]
    rg = _deep(main, "regimes")
    if not rg:
        return '<p class="empty">Not enough data to tell market regimes apart.</p>'
    sym = esc(main["config"]["symbol"])
    reg = {g["name"]: g for g in rg["regimes"]}
    base = _deep(runs[0], "regimes") if len(runs) > 1 else None
    breg = {g["name"]: g for g in base["regimes"]} if base else {}
    t = reg.get("turbulent")
    head = ""
    if t:
        head = (f'In turbulent markets (days when {sym}’s volatility over the previous 20 days was above '
                f'{pct(rg["cutoffs"]["turbulent_from"], 0)}), the stock’s volatility was <b>{pct(t["asset"]["volatility"], 0)}</b>'
                + (f', the strategy without rules <b>{pct(breg["turbulent"]["strategy"]["volatility"], 0)}</b>'
                   if "turbulent" in breg else "")
                + f', and {esc(short_label(main).lower())} <b>{pct(t["strategy"]["volatility"], 0)}</b>, holding '
                f'{t["avg_exposure"]:.0%} of the account on average.')
        c = reg.get("calm")
        if c and t["avg_exposure"] < c["avg_exposure"] - 0.05:
            head += f' It held more in calm markets ({c["avg_exposure"]:.0%}): it steps back exactly when risk is highest.'
        elif c and t["avg_exposure"] > c["avg_exposure"] + 0.05:
            head += (f' Note: it held <i>more</i> in turbulent markets than in calm ones ({c["avg_exposure"]:.0%}), so its '
                     f'risk rises when markets get wild.')
    rows = []
    for name in ("calm", "normal", "turbulent"):
        g = reg.get(name)
        if not g:
            continue
        bg = breg.get(name)
        rows.append(f'<tr><th>{name.title()}</th><td class="num">{g["share"]:.0%}</td>'
                    f'<td class="num">{pct(g["asset"]["volatility"], 0)}</td>'
                    + (f'<td class="num">{pct(bg["strategy"]["volatility"], 0)}</td>' if bg else "")
                    + f'<td class="num"><b>{pct(g["strategy"]["volatility"], 0)}</b></td>'
                    f'<td class="num">{loss(g["asset"]["worst_day"])}</td>'
                    + (f'<td class="num">{loss(bg["strategy"]["worst_day"])}</td>' if bg else "")
                    + f'<td class="num"><b>{loss(g["strategy"]["worst_day"])}</b></td>'
                    f'<td class="num">{g["avg_exposure"]:.0%}</td></tr>')
    ncol = 3 if breg else 2
    sub = (f'<th class="num">{sym}</th>' + ('<th class="num">No rules</th>' if breg else "")
           + f'<th class="num">{esc(short_label(main))}</th>')
    head_html = (f'<tr><th rowspan="2">Market</th><th rowspan="2" class="num">Days</th>'
                 f'<th colspan="{ncol}" class="grp">Volatility</th><th colspan="{ncol}" class="grp">Worst day</th>'
                 f'<th rowspan="2" class="num">Average<br>invested</th></tr><tr>{sub}{sub}</tr>')
    who = esc(short_label(main))
    items = []
    for name in ("calm", "normal", "turbulent"):
        g = reg.get(name)
        if g:
            items.append((f"{name.title()}: {sym}", g["asset"]["volatility"], "base"))
            items.append((f"{name.title()}: {who.lower()}", g["strategy"]["volatility"], "all"))
    return (f'<p class="verdict small-v">{head}</p>'
            f'<h3 class="h3">Volatility in each kind of market</h3>{_hbar_rows(items, lambda v: pct(v, 0))}'
            f'<div class="scroll"><table class="vart"><thead>{head_html}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="note">Calm, normal and turbulent are the thirds of all days by the stock’s volatility over the '
            f'previous 20 days (calm below {pct(rg["cutoffs"]["calm_below"], 0)}, turbulent from '
            f'{pct(rg["cutoffs"]["turbulent_from"], 0)}). Returns are averages over non-consecutive days, annualized.</p>')


def findings_block(runs) -> str:
    """The analysis in a few sentences, each linking to its evidence."""
    main = runs[-1]
    sym = esc(main["config"]["symbol"])
    f = []
    if len(runs) == 2 and not has_rules(runs[0]) and has_rules(runs[1]):
        a, b = runs[0]["summary"], runs[1]["summary"]
        f.append(("drawdowns", "shield", f'Risk rules changed the worst fall from {loss(a["max_drawdown"])} to '
                                          f'<b>{loss(b["max_drawdown"])}</b>, and volatility from {pct(a["volatility"])} '
                                          f'to <b>{pct(b["volatility"])}</b>.'))
    at = _deep(main, "attribution")
    if at and at["biggest"]:
        v = {x["key"]: x for x in at["variants"]}
        big = v[at["biggest"]]
        txt = f'Most of the protection came from the <b>{esc(big["label"].replace(" only", "").lower())}</b>.'
        if "breaker" in v and v["breaker"]["dd_cut"] < -0.005:
            txt += ' The circuit breaker alone made the worst fall <b>deeper</b>.'
        f.append(("rules", "layers", txt))
    rm = (main.get("risk_models") or {}).get("95")
    if rm:
        m = rm["models"]
        p, best = m["pnl_hist"], m[rm["best"]]
        if not p["passes"] and rm["best"] != "pnl_hist":
            f.append(("varcheck", "target", f'VaR from the account’s own history was beaten {p["exceptions"]} times when '
                                            f'{p["expected"]:.0f} were expected; <b>{esc(best["label"].lower())}</b> passes the '
                                            f'banks’ tests ({best["exceptions"]} vs {best["expected"]:.0f}).'))
        else:
            f.append(("varcheck", "target", f'The risk estimate holds up: <b>{esc(best["label"].lower())}</b> passes both bank '
                                            f'tests ({best["exceptions"]} breaches vs {best["expected"]:.0f} expected).'))
    t = _deep(main, "tails")
    if t:
        b3 = t["beyond"][1]
        if b3["ratio"] and b3["ratio"] >= 1.5:
            f.append(("tails", "tail", f'{sym} has <b>fat tails</b>: losses beyond 3 standard deviations happened '
                                       f'{b3["observed"]} times vs {b3["expected"]:.1f} for a bell curve ({_ratio_text(b3["ratio"])}).'))
    rb = _deep(main, "robustness")
    if rb and rb.get("n"):
        txt = (f'<b>{rb["beat_bh_sharpe"]} of {rb["n"]}</b> nearby settings beat buy & hold’s Sharpe ratio; yours ranks '
               f'{rb["chosen_rank"]} of {rb["n"]}.')
        h = rb.get("halves")
        if h:
            s1, s2 = h[0]["sharpe"], h[1]["sharpe"]
            same = (s1 > 0) == (s2 > 0) and min(abs(s1), abs(s2)) >= 0.5 * max(abs(s1), abs(s2))
            txt += f' It {"held up" if same else "<b>did not hold up</b>"} across both halves of the period.'
        f.append(("robust", "grid", txt))
    so = _deep(main, "sources")
    if so and so["beta"] < 0.98:
        f.append(("sources", "scale", f'Beta {so["beta"]:.2f}, alpha {pct(so["alpha_annual"], 1, signed=True)} a year '
                                      f'(t = {so["alpha_t"]:.1f}): ' + ("<b>significant</b>." if abs(so["alpha_t"]) >= 2 else
                                                                      "no evidence of skill beyond how much stock it held.")))
    rg = _deep(main, "regimes")
    if rg:
        reg = {g["name"]: g for g in rg["regimes"]}
        if "turbulent" in reg:
            tt = reg["turbulent"]
            f.append(("regimes", "cloud", f'In turbulent markets the stock’s volatility was {pct(tt["asset"]["volatility"], 0)}; '
                                          f'{esc(short_label(main).lower())}: <b>{pct(tt["strategy"]["volatility"], 0)}</b>.'))
    lt = main.get("luck_test")
    if lt:
        f.append(("luck", "dice", f'Luck test p = {lt["p_value"]:.2f}: ' + (
            "the timing looks <b>better than random</b>." if lt["p_value"] < 0.05 else
            "random timing did as well, so the strategy’s <b>timing could be luck</b>.")))
    if not f:
        return ""
    return ('<h3 class="h3">Key findings</h3><ul class="findings">' + "".join(
        f'<li><a href="#{sid}"><span class="fi">{icon(ic)}</span><span>{txt}</span><span class="go">→</span></a></li>'
        for sid, ic, txt in f) + '</ul>')


def events_block(runs) -> str:
    rows = []
    names = {"DRAWDOWN_BREAKER": ("alert", "Circuit breaker fired"),
             "RESUME": ("check", "Trading resumed"),
             "UNPRICEABLE": ("info", "No order (price rounds to $0)")}
    for k, r in enumerate(runs):
        for d, kind, detail in r["risk_events"]:
            if kind == "CASH_CLIP":
                continue
            ic, title = names.get(kind, ("info", kind))
            rows.append(f'<li class="ev {kind.lower()}"><span class="evi">{icon(ic)}</span>'
                        f'<div><div class="ev-t">{title} <span class="muted">· {esc(d)}</span></div>'
                        f'<div class="ev-d"><span class="sw s{k}bg"></span>{esc(short_label(r))}: '
                        f'{esc(detail)}</div></div></li>')
    if not rows:
        return '<p class="empty">No risk rule had to step in during this period.</p>'
    more = (f'<p class="note">{len(rows) - 40} more events are in the run’s saved result.</p>'
            if len(rows) > 40 else "")
    return f'<ul class="timeline">{"".join(rows[:40])}</ul>{more}'


# ----------------------------------------------------------------- render
def render(runs: list[dict], embed: bool = False) -> str:
    c0 = runs[0]["config"]
    s0 = runs[0]["summary"]
    sym = esc(c0["symbol"])
    period = f"{month_year(s0['start'])} to {month_year(s0['end'])}"
    compare = len(runs) == 2 and not has_rules(runs[0]) and has_rules(runs[1])

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
    ex_svg, ex_data = _chart("ex", runs, expo, 160, lambda v: pct(v, 0),
                             y_floor=0.0, y_ceil=1.0, events=False)

    legend = "".join(f'<span class="leg"><span class="sw s{k}bg"></span>{esc(label(r))}</span>'
                     for k, r in enumerate(runs))
    has_trips = any(e[1] == "DRAWDOWN_BREAKER" for r in runs for e in r["risk_events"])
    trip_leg = ('<span class="leg"><span class="sw tripbg"></span>Circuit breaker fired '
                '(shaded until trading resumed)</span>' if has_trips else "")

    # ---- summary tiles
    tiles = ""
    if compare:
        a, b = runs[0]["summary"], runs[1]["summary"]
        items = [("shield", "Max drawdown", "Biggest fall from a peak",
                  loss(a["max_drawdown"]), loss(b["max_drawdown"]), b["max_drawdown"] < a["max_drawdown"]),
                 ("wave", "Volatility", "How bumpy the ride is",
                  pct(a["volatility"]), pct(b["volatility"]), b["volatility"] < a["volatility"]),
                 ("gauge", "Sharpe ratio", "Return per unit of risk",
                  f"{a['sharpe']:.2f}", f"{b['sharpe']:.2f}", b["sharpe"] > a["sharpe"]),
                 ("up", "Annual return", "Average yearly growth",
                  pct(a["cagr"]), pct(b["cagr"]), b["cagr"] > a["cagr"])]
        tiles = '<div class="tiles">' + "".join(
            f'<div class="tile"><div class="tile-h"><span class="ti">{icon(ic)}</span>'
            f'<div><div class="tile-n">{n}</div><div class="tile-s">{sub}</div></div></div>'
            f'<div class="tile-v"><span class="was" title="No risk rules">{x}</span>'
            f'<span class="to">→</span><span class="now">{y}</span></div>{badge(good)}</div>'
            for ic, n, sub, x, y, good in items) + "</div>"
    else:
        s = runs[0]["summary"]
        items = [("shield", "Max drawdown", loss(s["max_drawdown"])),
                 ("wave", "Volatility", pct(s["volatility"])),
                 ("gauge", "Sharpe ratio", f"{s['sharpe']:.2f}"),
                 ("up", "Annual return", pct(s["cagr"]))]
        tiles = '<div class="tiles">' + "".join(
            f'<div class="tile"><div class="tile-h"><span class="ti">{icon(ic)}</span>'
            f'<div class="tile-n">{n}</div></div><div class="tile-v"><span class="now">{v}</span></div></div>'
            for ic, n, v in items) + "</div>"

    summary_explain = None
    if compare:
        a, b = runs[0]["summary"], runs[1]["summary"]
        cut = (a["max_drawdown"] - b["max_drawdown"]) * 100
        dr = (b["cagr"] - a["cagr"]) * 100
        if dr < 0:
            summary_explain = (
                f"Risk management is a trade: you usually give up some return to avoid deep losses. "
                f"Here the rules changed the worst fall by {cut:.1f} percentage points and cost "
                f"{abs(dr):.1f} points of annual return.")
        else:
            summary_explain = (
                f"Here the rules changed the worst fall by {cut:.1f} percentage points and annual "
                f"return also improved by {dr:.1f} points. Risk control does not always cost "
                f"return, but expect it to in strong, steady bull markets.")

    groups = [("Results", [("summary", "summary", "Summary"), ("drawdowns", "down", "Drawdowns"),
                           ("stress", "bolt", "Stress tests"), ("growth", "up", "Growth"),
                           ("invested", "pie", "Invested")]),
              ("Deep dive", [("varcheck", "target", "Risk model check"), ("tails", "tail", "Tail risk"),
                             ("rules", "layers", "What each rule does"), ("robust", "grid", "Robust or lucky?"),
                             ("sources", "scale", "Return sources"), ("regimes", "cloud", "Market regimes")]),
              ("Evidence", [("metrics", "table", "All metrics"), ("luck", "dice", "Luck test"),
                            ("log", "bell", "Risk-rule log"), ("method", "lock", "Method")])]
    nav_items = [it for _, items in groups for it in items]
    nav = "".join(f'<div class="ngrp">{g}</div>' + "".join(
        f'<a href="#{i}" data-view="{i}" title="{t}">{icon(ic, "nic")}<span>{t}</span></a>' for i, ic, t in items)
        for g, items in groups)

    secs = [
        section("summary", "summary", "Summary",
                f"{esc(strategy_name(runs[0]))} strategy on {sym}, {period}, starting with "
                f"${c0['initial_cash']:,.0f}.",
                f'<p class="verdict">{esc(verdict(runs))}</p>{tiles}{findings_block(runs)}', summary_explain),
        section("drawdowns", "down", "Drawdowns: how deep did it fall?",
                "The percentage lost since the last high point, every day.",
                f'<div class="legend">{legend}{trip_leg}</div>'
                f'<div class="figure">{dd_svg}<div class="tip" id="dd-tip"></div></div>'
                f'<h3 class="h3">The 5 worst drawdowns</h3>{drawdown_tables(runs)}',
                "Read it like water depth: the line sits at 0% when the money is at a new high "
                "and dips below whenever it is losing. Deeper and longer dips are what risk "
                "management tries to prevent. Orange triangles mark the days the circuit breaker fired."),
        section("stress", "bolt", "Stress tests: what happened in famous crashes?",
                f"Return over each sell-off, from the market's peak to its bottom.",
                stress_block(runs),
                "A stress test asks how a strategy would have survived a real crisis. These "
                "windows use the S&P 500's peak and bottom dates for each event."),
        section("growth", "up", f"Growth of ${c0['initial_cash']:,.0f}",
                "Account value over time, after fees.",
                f'<div class="figure">{eq_svg}<div class="tip" id="eq-tip"></div></div>'),
        section("invested", "pie", "How much was invested",
                f"Share of the money held in {sym} each day.",
                f'<div class="figure">{ex_svg}<div class="tip" id="ex-tip"></div></div>',
                "The risk rules hold less when the market is wild (volatility targeting) and step "
                "out completely for a while after a large loss (circuit breaker)."),
        section("metrics", "table", "All metrics",
                f"Daily data, 252 trading days a year. Fees {c0['costs']['fee_bps']:g} bp and "
                f"slippage {c0['costs']['slippage_bps']:g} bp per trade. Bold marks the better value.",
                metrics_table(runs)),
        section("varcheck", "target", "Risk model check: which way of measuring risk is right?",
                "Six ways to forecast tomorrow’s VaR, each made with data up to today only, graded by "
                "the tests banks must run.",
                var_block(runs),
                "A risk number is only useful if it is right. A correct 95% VaR is beaten on about "
                "1 day in 20, and those days should not bunch together. The Kupiec test asks whether "
                "the count is right, the Christoffersen test whether breaches cluster, and the Basel "
                "traffic light grades 99% VaR on the last 250 days (green 0–4 breaches, yellow 5–9, "
                "red 10 or more)."),
        section("luck", "dice", "Luck test: is the timing better than random?",
                f"We shuffle which days the strategy was invested "
                f"{(runs[0]['luck_test'] or {}).get('trials', 0):,} times and compare.",
                luck_block(runs),
                "If random timing often does as well as the strategy, its results could be luck. "
                "A p-value under 0.05 (the shaded zone) suggests the timing is real."),
        section("log", "bell", "Risk-rule log",
                "Every time a risk rule stepped in.", events_block(runs)),
        section("method", "lock", "Method and reproducibility",
                "How the numbers are made, and how to check them.",
                '<ul class="method"><li>Orders decided at a day’s close fill at the <b>next day’s '
                'open</b>, so the strategy never uses prices it could not have known.</li>'
                '<li>Money is counted in whole cents and checked every day: cash, shares and fees '
                'always add up.</li><li>Each run ID is a fingerprint of the settings, the exact '
                'data file and the engine code. The same inputs always give the identical result.</li>'
                '</ul><div class="scroll"><table class="mono"><thead><tr><th>Run</th><th>Run ID</th>'
                '<th>Data SHA-256</th><th>Engine</th></tr></thead><tbody>' + "".join(
                    f'<tr><td><span class="sw s{k}bg"></span>{esc(short_label(r))}</td>'
                    f'<td><code>{r["run_id"]}</code></td>'
                    f'<td><code>{r["manifest"]["dataset_sha256"][:16]}…</code></td>'
                    f'<td><code>{r["manifest"]["engine_version"]}</code></td></tr>'
                    for k, r in enumerate(runs)) + '</tbody></table></div>'),
    ]
    secs += [
        section("tails", "tail", "Tail risk: how often do big losses really happen?",
                "Daily returns compared with a bell curve of the same average and volatility.",
                tails_block(runs),
                "Many risk models assume returns follow a bell curve (normal distribution). Real markets "
                "have fat tails: quiet most of the time, with crashes far more often than the bell curve "
                "allows. σ (sigma) is one standard deviation of daily returns."),
        section("rules", "layers", "What each rule does on its own",
                "The same strategy with no rules, each rule alone, and all rules together.",
                rules_block(runs),
                "Risk rules interact. Running each one alone shows which rule actually protects you and "
                "what it costs, instead of crediting the whole package."),
        section("robust", "grid", "Robust or lucky?",
                "The same test with nearby settings, and the period split in two halves.",
                robust_block(runs),
                "A result that only works for one exact setting is probably luck (overfitting). A robust "
                "strategy does about as well with nearby settings and in both halves of the data."),
        section("sources", "scale", "Where do the returns come from?",
                "The strategy’s daily returns regressed on the stock’s.",
                sources_block(runs),
                "Beta is market exposure: how much the strategy moves when the stock moves. Alpha is the "
                "return left over after accounting for that exposure. A t-statistic above 2 (or below −2) "
                "means the alpha is unlikely to be noise."),
        section("regimes", "cloud", "Market regimes: when does risk show up?",
                "Calm, normal and turbulent markets, by the stock’s recent volatility.",
                regimes_block(runs),
                "Averages hide when risk arrives. Good risk control matters most in turbulent markets, "
                "which is when losses cluster."),
    ]
    by_id = {re.search(r'<section class="card" id="([a-z]+)"', x).group(1): x for x in secs}
    secs = [by_id[i] for i, _, _ in nav_items]

    # pager: back / next buttons at the bottom of every page
    pages = []
    for n, sec in enumerate(secs):
        prev_ = nav_items[n - 1] if n > 0 else None
        next_ = nav_items[n + 1] if n + 1 < len(nav_items) else None
        pager = '<div class="pager">' + (
            f'<a class="pg prev" href="#{prev_[0]}"><span class="pgk">Back</span>{prev_[2]}</a>'
            if prev_ else '<span></span>') + (
            f'<a class="pg next" href="#{next_[0]}"><span class="pgk">Next</span>{next_[2]}</a>'
            if next_ else '<span></span>') + '</div>'
        i = sec.rindex("</section>")
        pages.append(sec[:i] + pager + sec[i:])
    body = "".join(pages)

    css_series = "".join(f".s{k}{{stroke:{c[0]}}}.s{k}f{{fill:{c[0]}}}.s{k}bg{{background:{c[0]}}}"
                         for k, c in enumerate(SERIES))
    css_series_dark = "".join(f".s{k}{{stroke:{c[1]}}}.s{k}f{{fill:{c[1]}}}.s{k}bg{{background:{c[1]}}}"
                              for k, c in enumerate(SERIES))
    data_json = json.dumps({"dd": dd_data, "eq": eq_data, "ex": ex_data,
                            "labels": [short_label(r) for r in runs]})
    accent = SERIES[1][0]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RiskLens report: {sym}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{color-scheme:light;--bg:#f3f6f9;--panel:#ffffff;--ink:#14213d;--ink2:#4a5568;--ink3:#7b8796;
--rule:#e1e7ee;--grid:#edf1f5;--halt:rgba(199,119,0,.12);--trip:#c77700;--good:#0f7b4f;--goodbg:#e6f4ec;
--bad:#b3261e;--badbg:#fbeaea;--accent:{accent};--accentbg:#e3f2f1;--tipbg:#f6f9fb}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#0c1117;--panel:#141b23;--ink:#e6ebf0;
--ink2:#b3bdc8;--ink3:#7f8b98;--rule:#253039;--grid:#1d2630;--halt:rgba(240,170,60,.14);--trip:#f0aa3c;
--good:#4cc38a;--goodbg:#13281e;--bad:#ff8a80;--badbg:#2e1716;--accent:{SERIES[1][1]};--accentbg:#12302e;--tipbg:#18212b}}}}
{css_series}
@media (prefers-color-scheme:dark){{{css_series_dark}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}}
.app{{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}}
.side{{position:sticky;top:0;height:100vh;overflow-y:auto;background:var(--panel);border-right:1px solid var(--rule);
padding:22px 14px;display:flex;flex-direction:column;gap:18px}}
.brand{{display:flex;align-items:center;gap:10px;font-weight:600;font-size:18px;padding:0 8px}}
.brand svg{{width:30px;height:30px;color:var(--accent);flex:none}}
.brand small{{display:block;font-weight:400;font-size:13px;color:var(--ink2)}}
nav{{display:flex;flex-direction:column;gap:2px}}
nav a{{display:flex;align-items:center;gap:12px;color:var(--ink2);text-decoration:none;font-size:15px;
padding:10px 12px;border-radius:10px}}
nav a:hover{{background:var(--tipbg);color:var(--ink)}}
nav a.on{{background:var(--accentbg);color:var(--accent);font-weight:600}}
.nic{{width:20px;height:20px;flex:none}}
.side-foot{{margin-top:auto;font-size:12px;color:var(--ink3);padding:0 8px;line-height:1.5}}
main{{padding:32px 36px 64px;max-width:1120px;width:100%}}
.js .card{{display:none}} .js .card.on{{display:block}}
.pager{{display:flex;justify-content:space-between;gap:12px;margin-top:28px;padding-top:20px;border-top:1px solid var(--rule)}}
.pg{{display:flex;flex-direction:column;text-decoration:none;color:var(--ink);font-weight:600;padding:10px 16px;
border:1px solid var(--rule);border-radius:12px;min-width:150px}}
.pg:hover{{border-color:var(--accent);color:var(--accent)}} .pg.next{{text-align:right;margin-left:auto}}
.pgk{{font-size:12px;font-weight:400;color:var(--ink3)}}
.embed{{display:block}} .embed .side{{position:sticky;top:0;height:auto;flex-direction:row;align-items:center;padding:8px 12px;
border-right:0;border-bottom:1px solid var(--rule);z-index:5}}
.embed .brand,.embed .side-foot{{display:none}} .embed nav{{flex-direction:row;overflow-x:auto;gap:2px}}
.embed nav a{{padding:8px 12px;font-size:14px}} .embed main{{padding:20px 20px 40px;max-width:none}}
@media print{{.side,.pager{{display:none}} .app{{display:block}} .js .card{{display:block}}}}
.card{{background:var(--panel);border:1px solid var(--rule);border-radius:16px;padding:30px 32px;margin:0 0 22px;scroll-margin-top:90px}}
.card-h{{display:flex;gap:16px;align-items:flex-start;margin-bottom:18px}}
.ib{{flex:none;width:44px;height:44px;border-radius:12px;background:var(--accentbg);color:var(--accent);display:grid;place-items:center}}
.ic{{width:24px;height:24px}}
h2{{font-size:21px;line-height:1.3;font-weight:600;margin:2px 0 2px}}
.h3,h3{{font-size:16px;font-weight:600;margin:28px 0 10px}}
.sub{{color:var(--ink2);margin:0;font-size:15px}}
.verdict{{font-size:24px;line-height:1.4;font-weight:500;margin:4px 0 24px;max-width:36ch}}
.tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
.tile{{border:1px solid var(--rule);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:10px}}
.tile-h{{display:flex;gap:10px;align-items:center}}
.ti{{width:34px;height:34px;border-radius:10px;background:var(--tipbg);color:var(--ink2);display:grid;place-items:center;flex:none}}
.ti svg{{width:20px;height:20px}}
.tile-n{{font-weight:600;font-size:15px;line-height:1.2}} .tile-s{{font-size:13px;color:var(--ink3)}}
.tile-v{{display:flex;align-items:baseline;gap:8px;white-space:nowrap}}
.was{{color:var(--ink3);font-size:16px}} .to{{color:var(--ink3)}}
.now{{font-size:26px;font-weight:600;color:var(--accent);letter-spacing:-.01em}}
.badge{{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:500;padding:3px 10px 3px 6px;border-radius:999px;align-self:flex-start;white-space:nowrap}}
.badge.good{{background:var(--goodbg);color:var(--good)}} .badge.bad{{background:var(--badbg);color:var(--bad)}}
.bi{{width:16px;height:16px}}
.explain{{display:flex;gap:12px;align-items:flex-start;background:var(--tipbg);border-radius:12px;padding:14px 16px;margin-top:20px}}
.explain p{{margin:0;color:var(--ink2);font-size:15px}} .ei{{width:22px;height:22px;flex:none;color:var(--trip)}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 22px;font-size:14px;color:var(--ink2);margin:0 0 12px}}
.leg{{display:inline-flex;align-items:center}}
.sw{{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:8px;flex:none;vertical-align:-1px}}
.tripbg{{background:var(--halt);border:2px solid var(--trip)}}
.figure{{position:relative;border:1px solid var(--rule);border-radius:12px;padding:14px 12px 6px}}
.chart{{display:block;width:100%;height:auto;overflow:visible}}
.grid{{stroke:var(--grid);stroke-width:1}} .grid.zero{{stroke:var(--ink3)}} .ytick{{stroke:var(--ink3)}}
.tick{{fill:var(--ink3);font-size:12px}}
.line{{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}} .fill{{opacity:.16;stroke:none}}
.halt{{fill:var(--halt)}} .trip{{fill:var(--trip)}}
.xhair{{stroke:var(--ink3);stroke-width:1;stroke-dasharray:3 3}} .hit{{fill:transparent;cursor:crosshair}}
.tip{{position:absolute;top:10px;pointer-events:none;background:var(--panel);border:1px solid var(--rule);
border-radius:10px;padding:8px 10px;font-size:13px;display:none;box-shadow:0 6px 18px rgba(0,0,0,.10);min-width:160px}}
.tip b{{display:block;margin-bottom:4px;font-weight:600}}
.tip div{{display:flex;align-items:center;justify-content:space-between;gap:12px}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:15px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--rule);vertical-align:top}}
thead th{{font-weight:600;font-size:13px;color:var(--ink2);background:var(--tipbg)}}
thead th:first-child{{border-top-left-radius:8px}} thead th:last-child{{border-top-right-radius:8px}}
tbody th{{font-weight:500}}
.num{{text-align:right;white-space:nowrap}} .strong{{font-weight:600}} td.best{{font-weight:600;color:var(--accent)}}
.muted{{color:var(--ink3)}} .small{{font-size:13px;font-weight:400}}
tr.group th{{font-weight:600;color:var(--accent);padding-top:22px;font-size:14px}}
.meaning{{color:var(--ink2);font-size:14px;min-width:240px}}
.ddgrid{{display:grid;grid-template-columns:1fr;gap:22px}} .ddt td{{white-space:nowrap}}
.ddt h3{{margin:0 0 8px;font-size:15px}}
.stress td .badge{{font-size:12px}}
.luck{{border:1px solid var(--rule);border-radius:12px;padding:16px 18px;margin:0 0 14px}}
.luck-h{{display:flex;align-items:center;gap:6px;margin-bottom:6px}} .luck-h .badge{{margin-left:auto}}
.luck p{{margin:0 0 14px;color:var(--ink2);font-size:15px}}
.pbar{{position:relative;height:10px;border-radius:5px;background:var(--grid)}}
.pbar .zone{{position:absolute;left:0;top:0;bottom:0;width:5%;background:var(--goodbg);border-right:2px solid var(--good);border-radius:5px 0 0 5px}}
.pbar .dot{{position:absolute;top:-5px;width:20px;height:20px;border-radius:50%;margin-left:-10px;border:3px solid var(--panel)}}
.pscale{{display:flex;justify-content:space-between;font-size:12px;color:var(--ink3);margin-top:8px;position:relative}}
.thrlab{{position:absolute;left:5%;transform:translateX(-50%)}} .pv{{color:var(--ink);font-weight:500}}
.vbar{{stroke:var(--ink3);stroke-width:1;opacity:.45;fill:none}} .vbarbg{{background:var(--ink3)}}
.varl{{stroke:var(--accent);stroke-width:1.6}} .vx{{fill:var(--bad)}} .vxbg{{background:var(--bad);border-radius:50%}}
.small-v{{font-size:18px;max-width:none}}
.zone{{display:inline-block;font-size:12px;font-weight:600;border-radius:999px;padding:1px 9px}}
.zone.green{{background:var(--goodbg);color:var(--good)}} .zone.yellow{{background:#fdf3e1;color:#b26a00}}
.zone.red{{background:var(--badbg);color:var(--bad)}}
.vart td,.vart th{{white-space:nowrap}} .vart th.grp{{text-align:center;border-bottom:1px solid var(--rule)}}
.vart tr.bestrow th,.vart tr.bestrow td{{background:var(--accentbg)}}
.tag{{display:inline-block;font-size:11px;font-weight:600;border-radius:6px;padding:1px 7px;background:var(--accent);color:#fff;margin-left:6px;vertical-align:1px}}
.vtabs{{display:flex;gap:6px;flex-wrap:wrap;margin:16px 0 10px}}
.vt{{border:1px solid var(--rule);background:var(--panel);border-radius:999px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer;color:var(--ink2)}}
.vt.on{{background:var(--accent);border-color:var(--accent);color:#fff}}
.vfig{{display:none}} .vfig.on{{display:block}}
.ngrp{{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);padding:14px 12px 4px}}
.ngrp:first-child{{padding-top:2px}}
.embed .ngrp{{display:none}}
.findings{{list-style:none;padding:0;margin:0;display:grid;gap:8px}}
.findings a{{display:flex;gap:12px;align-items:flex-start;text-decoration:none;color:var(--ink);border:1px solid var(--rule);
border-radius:12px;padding:12px 14px;font-size:15px}}
.findings a:hover{{border-color:var(--accent)}}
.findings .fi{{width:30px;height:30px;border-radius:8px;background:var(--accentbg);color:var(--accent);display:grid;place-items:center;flex:none}}
.findings .fi svg{{width:18px;height:18px}}
.findings .go{{margin-left:auto;color:var(--ink3);padding-left:8px}}
.hbar{{fill:var(--ink3);opacity:.55}} .hbar.hot{{fill:var(--bad);opacity:.85}} .normc{{stroke:var(--accent);stroke-width:2}}
.hbarbg{{background:var(--ink3)}} .hotbg{{background:var(--bad)}} .normbg{{background:var(--accent)}}
.hbars{{display:grid;gap:8px;margin:6px 0 4px}}
.hb{{display:grid;grid-template-columns:minmax(150px,240px) minmax(0,1fr) 80px;gap:12px;align-items:center;font-size:14px}}
.hb .ht{{background:var(--grid);border-radius:6px;height:14px;overflow:hidden}}
.hb .ht i{{display:block;height:100%;background:var(--ink3);border-radius:6px}}
.hb.all .ht i{{background:var(--accent)}} .hb.base .ht i{{background:var(--base, #7a8494)}}
.hb .hv{{text-align:right;font-weight:600}}
.heat{{width:auto;border-collapse:separate;border-spacing:3px}}
.heat th{{font-size:12px;color:var(--ink3);font-weight:600;background:none;padding:4px 8px;text-align:center}}
.heat td{{text-align:center;font-size:13px;padding:8px 10px;border-radius:6px;border:0;
background:color-mix(in srgb,var(--accent) calc(var(--q) * 70%),var(--panel));min-width:58px}}
.heat td.me{{outline:2.5px solid var(--ink);outline-offset:-2px;font-weight:700}}
.heat td.na{{background:none;color:var(--ink3)}}
.sc{{fill:var(--ink3);opacity:.35}} .scbg{{background:var(--ink3)}} .fit{{stroke:var(--accent);stroke-width:2.5}}
.timeline{{list-style:none;padding:0;margin:0}}
.ev{{display:flex;gap:14px;padding:12px 0;border-bottom:1px solid var(--rule)}}
.evi{{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--tipbg);color:var(--ink2)}}
.evi svg{{width:18px;height:18px}}
.ev.drawdown_breaker .evi{{background:var(--halt);color:var(--trip)}} .ev.resume .evi{{background:var(--goodbg);color:var(--good)}}
.ev-t{{font-weight:600}} .ev-d{{color:var(--ink2);font-size:14px}}
.empty{{color:var(--ink2);background:var(--tipbg);border-radius:10px;padding:14px 16px;margin:0}}
.note{{color:var(--ink2);font-size:14px;margin:12px 0 0}}
.method{{margin:0 0 18px;padding-left:20px;color:var(--ink2)}} .method li{{margin:4px 0}}
code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
@media (max-width:980px){{.tiles{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:820px){{.app{{display:block}} .side{{position:sticky;height:auto;flex-direction:row;align-items:center;
padding:10px 12px;gap:10px;border-right:0;border-bottom:1px solid var(--rule);z-index:5}}
.brand small,.side-foot,nav a span{{display:none}} nav{{flex-direction:row;overflow-x:auto}} nav a{{padding:9px}}
main{{padding:18px 12px 48px}}}}
@media (max-width:560px){{.card{{padding:20px 16px}} .verdict{{font-size:19px}} .tiles{{grid-template-columns:1fr}}
.pg{{min-width:0}}}}
</style><script>document.documentElement.classList.add('js')</script></head><body>
<div class="app{" embed" if embed else ""}">
<aside class="side">
<div class="brand">{icon("shield")}<div>RiskLens<small>{sym} risk report</small></div></div>
<nav aria-label="Report sections">{nav}</nav>
<div class="side-foot">{period}<br>Simulated results, not investment advice.</div>
</aside>
<main>{body}</main>
</div>
<script>
const views=[...document.querySelectorAll('.card')], links=[...document.querySelectorAll('nav a')];
function go(){{
  const id=(location.hash||'#summary').slice(1), v=views.find(x=>x.id===id)||views[0];
  views.forEach(x=>x.classList.toggle('on',x===v));
  links.forEach(a=>{{const on=a.dataset.view===v.id; a.classList.toggle('on',on);
    if(on) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');}});
  requestAnimationFrame(()=>window.scrollTo(0,0));
}}
window.addEventListener('hashchange',go); window.addEventListener('load',go); go();
document.querySelectorAll('.vt').forEach(b=>b.addEventListener('click',()=>{{
  document.querySelectorAll('.vt').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('.vfig').forEach(f=>f.classList.toggle('on',f.id===b.dataset.show));}}));
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
