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

    nav_items = [("summary", "summary", "Summary"), ("drawdowns", "down", "Drawdowns"),
                 ("stress", "bolt", "Stress tests"), ("growth", "up", "Growth"),
                 ("invested", "pie", "Invested"), ("metrics", "table", "All metrics"),
                 ("luck", "dice", "Luck test"), ("log", "bell", "Risk-rule log"),
                 ("method", "lock", "Method")]
    nav = "".join(f'<a href="#{i}" data-view="{i}">{icon(ic, "nic")}<span>{t}</span></a>'
                  for i, ic, t in nav_items)

    secs = [
        section("summary", "summary", "Summary",
                f"{esc(strategy_name(runs[0]))} strategy on {sym}, {period}, starting with "
                f"${c0['initial_cash']:,.0f}.",
                f'<p class="verdict">{esc(verdict(runs))}</p>{tiles}', summary_explain),
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
