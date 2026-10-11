"""The RiskLens web app, served by the run server at http://127.0.0.1:8765/

One self-contained page (no build step, no external JS) with four screens:
  Overview   risk snapshot of every downloaded dataset (watchlist cards)
  Analyze    controls for stock, strategy and risk rules; runs and shows the report
  Live       replays history through the engine day by day, streaming risk
             metrics and alerts like a risk desk monitor (Server-Sent Events)
  History    every stored run, reopenable
All numbers come from the server's API; the page only displays them.
"""
from __future__ import annotations

import json

from .report import _ICON_PATHS


def _icons_json() -> str:
    return json.dumps(_ICON_PATHS | {
        "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
                '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
        "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/>'
                   '<circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
        "pulse": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "play": '<path d="M8 5l11 7-11 7z"/>',
        "pause": '<path d="M8 5v14M16 5v14"/>',
        "restart": '<path d="M4 12a8 8 0 1 0 3-6.2"/><path d="M4 4v5h5"/>',
        "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    })


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RiskLens</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light;--bg:#f2f5f8;--panel:#fff;--ink:#14213d;--ink2:#4a5568;--ink3:#7b8796;--rule:#e1e7ee;
--grid:#edf1f5;--soft:#f6f9fb;--nav:#0f1b2d;--nav2:#16263d;--navink:#b9c6d5;--accent:#0f7b7b;--accent2:#e3f2f1;
--base:#7a8494;--good:#0f7b4f;--goodbg:#e6f4ec;--warn:#b26a00;--warnbg:#fdf3e1;--bad:#b3261e;--badbg:#fbeaea}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0b1016;--panel:#131a22;--ink:#e6ebf0;--ink2:#b3bdc8;
--ink3:#7f8b98;--rule:#232e39;--grid:#1c2630;--soft:#18212b;--nav:#080d13;--nav2:#121b26;--accent:#3fb8af;--accent2:#12302e;
--base:#9aa4b1;--good:#4cc38a;--goodbg:#13281e;--warn:#f0aa3c;--warnbg:#2d2210;--bad:#ff8a80;--badbg:#2e1716}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}
.ic{width:20px;height:20px;flex:none}
button,select,input{font:inherit;color:inherit}
.shell{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}
.nav{background:var(--nav);color:var(--navink);position:sticky;top:0;height:100vh;display:flex;flex-direction:column;padding:20px 12px;gap:6px}
.brand{display:flex;align-items:center;gap:10px;color:#fff;font-weight:600;font-size:18px;padding:4px 10px 18px}
.brand .ic{width:28px;height:28px;color:#3fb8af}
.nav a{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:10px;color:var(--navink);text-decoration:none}
.nav a:hover{background:var(--nav2);color:#fff}
.nav a.on{background:#3fb8af22;color:#fff;font-weight:600;box-shadow:inset 3px 0 0 #3fb8af}
.navfoot{margin-top:auto;font-size:12px;padding:10px;line-height:1.5;color:#7f8fa3}
.navfoot b{color:#cfe0ea;font-weight:500}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4cc38a;margin-right:6px;vertical-align:1px}
.dot.off{background:#ff8a80}
.main{min-width:0}
.top{display:flex;align-items:center;gap:16px;padding:22px 32px 6px}
.top h1{font-size:24px;font-weight:600;margin:0;line-height:1.2}
.top p{margin:2px 0 0;color:var(--ink2)}
.top .spacer{flex:1}
.page{display:none;padding:16px 32px 48px}
.page.on{display:block}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:14px}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--rule);background:var(--panel);border-radius:10px;
padding:8px 14px;cursor:pointer;font-weight:500;text-decoration:none;color:var(--ink)}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{filter:brightness(1.08);color:#fff}
.btn .ic{width:18px;height:18px}
.btn[disabled]{opacity:.55;cursor:default}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
/* overview */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:18px}
.tk{padding:18px 18px 16px;display:flex;flex-direction:column;gap:12px}
.tk-h{display:flex;align-items:flex-start;gap:10px}
.sym{font-size:22px;font-weight:600;letter-spacing:.01em}
.px{color:var(--ink2);font-size:14px}
.pill{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;border-radius:999px;padding:3px 10px 3px 8px}
.pill .ic{width:15px;height:15px}
.pill.calm{background:var(--goodbg);color:var(--good)} .pill.elevated{background:var(--warnbg);color:var(--warn)}
.pill.stressed,.pill.halted{background:var(--badbg);color:var(--bad)}
.spark{width:100%;height:56px;display:block}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;font-size:13px}
.stats div{display:flex;justify-content:space-between;border-bottom:1px solid var(--grid);padding-bottom:4px}
.stats span{color:var(--ink3)} .stats b{font-weight:600}
.tk-a{display:flex;gap:8px;margin-top:2px}
.tk-a .btn{flex:1;justify-content:center;padding:7px 10px;font-size:14px}
.note{color:var(--ink2);font-size:14px}
.empty{padding:28px;text-align:center;color:var(--ink2)}
.empty code{background:var(--soft);padding:2px 6px;border-radius:6px}
/* analyze */
.an{display:grid;grid-template-columns:340px minmax(0,1fr);gap:20px;align-items:start}
.form{padding:20px;position:sticky;top:16px}
.form h3{font-size:13px;font-weight:600;color:var(--ink3);margin:18px 0 8px;letter-spacing:.02em}
.form h3:first-child{margin-top:0}
.f{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}
.f label{font-size:13px;color:var(--ink2)}
.f select,.f input[type=number],.f input[type=date]{border:1px solid var(--rule);border-radius:8px;padding:7px 10px;background:var(--panel);width:100%}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.seg{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.seg button{border:0;background:var(--panel);padding:8px 4px;cursor:pointer;font-size:13px;border-right:1px solid var(--rule)}
.seg button:last-child{border-right:0}
.seg button.on{background:var(--accent);color:#fff;font-weight:600}
.rule{border:1px solid var(--rule);border-radius:10px;padding:10px 12px;margin-bottom:8px}
.rule-h{display:flex;align-items:center;gap:8px;font-weight:500;font-size:14px}
.rule-h .val{margin-left:auto;font-weight:600;color:var(--accent)}
.rule input[type=range]{width:100%;accent-color:var(--accent);margin-top:6px}
.rule.off .val,.rule.off input[type=range]{opacity:.35}
.sw{position:relative;width:34px;height:20px;flex:none}
.sw input{opacity:0;width:0;height:0;position:absolute}
.sw span{position:absolute;inset:0;background:var(--rule);border-radius:999px;transition:.15s;cursor:pointer}
.sw span:before{content:"";position:absolute;width:14px;height:14px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.15s}
.sw input:checked+span{background:var(--accent)} .sw input:checked+span:before{transform:translateX(14px)}
.sw input:focus-visible+span{outline:2px solid var(--accent);outline-offset:2px}
.runbar{display:flex;gap:10px;align-items:center;margin-top:14px}
.runbar .btn.primary{flex:1;justify-content:center;padding:10px}
.result{min-height:70vh;overflow:hidden}
.result iframe{width:100%;height:calc(100vh - 150px);border:0;display:block}
.placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:10px;
height:60vh;color:var(--ink2);padding:24px}
.placeholder .ic{width:40px;height:40px;color:var(--accent)}
.msg{font-size:13px;color:var(--ink2);min-height:20px;margin-top:8px}
.msg.err{color:var(--bad)}
/* live */
.lv-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:12px 14px;margin-bottom:16px}
.lv-bar select{border:1px solid var(--rule);border-radius:8px;padding:7px 10px;background:var(--panel)}
.lv-bar .chip{font-size:13px;color:var(--ink2);background:var(--soft);border-radius:8px;padding:6px 10px}
.lv-bar .chip a{color:var(--accent)}
.lv-bar .grow{flex:1}
.clock{display:flex;align-items:baseline;gap:14px;margin:0 0 14px}
.clock .date{font-size:30px;font-weight:600;letter-spacing:.01em}
.clock .live{font-size:13px;font-weight:600;color:var(--ink3);display:flex;align-items:center;gap:6px}
.clock .live i{width:9px;height:9px;border-radius:50%;background:var(--ink3);display:inline-block}
.clock .live.on{color:var(--bad)} .clock .live.on i{background:var(--bad);animation:pulse 1.2s infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.clock .live.on i{animation:none}}
.prog{height:6px;background:var(--grid);border-radius:3px;overflow:hidden;margin:-6px 0 18px}
.prog i{display:block;height:100%;width:0;background:var(--accent)}
.lv{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;align-items:start}
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px}
.kpi{padding:14px 16px}
.kpi .k{font-size:13px;color:var(--ink2);display:flex;align-items:center;gap:6px}
.kpi .k .ic{width:16px;height:16px;color:var(--ink3)}
.kpi .v{font-size:24px;font-weight:600;margin-top:2px}
.kpi .s{font-size:13px;color:var(--ink3)}
.kpi .s b{color:var(--base);font-weight:600}
.chartcard{padding:14px 16px 10px;margin-bottom:16px}
.chartcard h4{margin:0 0 8px;font-size:14px;font-weight:600;display:flex;gap:16px;align-items:center}
.lg{display:inline-flex;align-items:center;gap:6px;font-weight:400;color:var(--ink2);font-size:13px}
.lg i{width:10px;height:10px;border-radius:2px;display:inline-block}
canvas{width:100%;display:block}
.alerts{padding:16px;position:sticky;top:16px;max-height:calc(100vh - 32px);display:flex;flex-direction:column}
.alerts h4{margin:0 0 10px;font-size:15px;display:flex;align-items:center;gap:8px}
.alerts h4 .count{margin-left:auto;font-size:12px;color:var(--ink3);font-weight:400}
.feed{list-style:none;margin:0;padding:0;overflow-y:auto}
.feed li{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid var(--grid);font-size:13px}
.feed .ai{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;flex:none}
.feed .ai .ic{width:16px;height:16px}
.feed .crit .ai{background:var(--badbg);color:var(--bad)} .feed .warn .ai{background:var(--warnbg);color:var(--warn)}
.feed .ok .ai{background:var(--goodbg);color:var(--good)} .feed .info .ai{background:var(--soft);color:var(--ink2)}
.feed b{display:block;font-weight:600;font-size:14px}
.feed span{color:var(--ink3)}
.endbox{padding:14px 16px;margin-bottom:16px;display:none;border-left:4px solid var(--accent)}
.endbox.on{display:block}
/* history */
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--rule);font-size:14px;white-space:nowrap}
thead th{font-size:12px;color:var(--ink2);font-weight:600;background:var(--soft)}
td.num,th.num{text-align:right}
.scroll{overflow-x:auto}
@media (max-width:1100px){.lv{grid-template-columns:1fr} .alerts{position:static;max-height:420px}}
@media (max-width:900px){.an{grid-template-columns:1fr} .form{position:static} .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.shell{display:block} .nav{position:sticky;height:auto;flex-direction:row;align-items:center;padding:8px;z-index:9;overflow-x:auto}
.brand{padding:4px 8px} .brand span,.nav a span,.navfoot{display:none} .top,.page{padding-left:14px;padding-right:14px}}
</style></head><body>
<div class="shell">
<aside class="nav" aria-label="Main">
  <div class="brand"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/></svg><span>RiskLens</span></div>
  <a href="#/overview" data-p="overview"><i data-i="grid"></i><span>Overview</span></a>
  <a href="#/analyze" data-p="analyze"><i data-i="sliders"></i><span>Analyze</span></a>
  <a href="#/live" data-p="live"><i data-i="pulse"></i><span>Live monitor</span></a>
  <a href="#/history" data-p="history"><i data-i="clock"></i><span>History</span></a>
  <div class="navfoot"><span class="dot" id="hdot"></span><b id="hstat">Engine online</b><br>
  engine <span id="hver">__VERSION__</span><br><span id="hcount"></span><br><br>Simulated results on historical data. Not investment advice.</div>
</aside>
<div class="main">
  <header class="top"><div><h1 id="ptitle">Overview</h1><p id="psub"></p></div></header>

  <section class="page" id="p-overview">
    <div class="cards" id="cards"><div class="card empty">Loading datasets…</div></div>
  </section>

  <section class="page" id="p-analyze">
    <div class="an">
      <form class="card form" id="form" onsubmit="return false">
        <h3>Market</h3>
        <div class="f"><label for="f-sym">Stock or fund</label><select id="f-sym"></select></div>
        <div class="row2">
          <div class="f"><label for="f-start">From</label><input type="date" id="f-start"></div>
          <div class="f"><label for="f-end">To</label><input type="date" id="f-end"></div>
        </div>
        <h3>Strategy</h3>
        <div class="seg" id="f-strat" role="group" aria-label="Strategy">
          <button type="button" data-v="sma_cross">Trend</button>
          <button type="button" data-v="momentum">Momentum</button>
          <button type="button" data-v="buy_and_hold">Buy &amp; hold</button>
        </div>
        <div class="row2" id="p-sma" style="margin-top:10px">
          <div class="f"><label for="f-fast">Fast average (days)</label><input type="number" id="f-fast" min="1" max="400" value="50"></div>
          <div class="f"><label for="f-slow">Slow average (days)</label><input type="number" id="f-slow" min="2" max="1000" value="200"></div>
        </div>
        <div class="f" id="p-mom" style="margin-top:10px"><label for="f-look">Look-back (days)</label><input type="number" id="f-look" min="1" max="1000" value="126"></div>
        <h3>Risk rules</h3>
        <div class="rule" id="r-vol"><div class="rule-h"><label class="sw"><input type="checkbox" id="f-vol-on" checked><span></span></label>
          <label for="f-vol">Volatility target</label><span class="val" id="v-vol"></span></div>
          <input type="range" id="f-vol" min="5" max="40" step="1" value="15" aria-label="Volatility target percent"></div>
        <div class="rule" id="r-dd"><div class="rule-h"><label class="sw"><input type="checkbox" id="f-dd-on" checked><span></span></label>
          <label for="f-dd">Drawdown circuit breaker</label><span class="val" id="v-dd"></span></div>
          <input type="range" id="f-dd" min="5" max="40" step="1" value="15" aria-label="Drawdown limit percent"></div>
        <div class="rule" id="r-cool"><div class="rule-h"><label for="f-cool">Pause after breaker</label><span class="val" id="v-cool"></span></div>
          <input type="range" id="f-cool" min="5" max="60" step="1" value="20" aria-label="Cooldown days"></div>
        <div class="rule" id="r-cap"><div class="rule-h"><label for="f-cap">Max share invested</label><span class="val" id="v-cap"></span></div>
          <input type="range" id="f-cap" min="10" max="100" step="5" value="100" aria-label="Maximum exposure percent"></div>
        <h3>Costs and testing</h3>
        <div class="row2">
          <div class="f"><label for="f-fee">Fee (bp)</label><input type="number" id="f-fee" min="0" max="100" step="0.5" value="1"></div>
          <div class="f"><label for="f-slip">Slippage (bp)</label><input type="number" id="f-slip" min="0" max="100" step="0.5" value="2"></div>
        </div>
        <div class="f"><label for="f-luck">Luck test</label><select id="f-luck">
          <option value="1000">1,000 shuffles (about 1 s)</option><option value="200">200 shuffles (fast)</option><option value="0">Off</option></select></div>
        <div class="runbar"><button class="btn primary" id="run" type="button"><i data-i="play"></i>Run analysis</button>
          <a class="btn" id="tolive" href="#/live" title="Replay these settings live"><i data-i="pulse"></i></a></div>
        <div class="msg" id="amsg" role="status"></div>
      </form>
      <div class="card result" id="result"><div class="placeholder"><i data-i="sliders"></i>
        <b>Set up a strategy and its risk rules, then run the analysis.</b>
        <span>Results compare the same strategy with and without the rules.</span></div></div>
    </div>
  </section>

  <section class="page" id="p-live">
    <div class="card lv-bar">
      <select id="l-sym" aria-label="Stock"></select>
      <span class="chip" id="l-cfg"></span>
      <span class="grow"></span>
      <label class="note" for="l-speed">Speed</label>
      <select id="l-speed"><option value="10">Slow</option><option value="40" selected>Normal</option>
        <option value="150">Fast</option><option value="600">Very fast</option></select>
      <label class="note" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="l-loop" checked> Loop</label>
      <button class="btn" id="l-restart" type="button"><i data-i="restart"></i>Restart</button>
      <button class="btn primary" id="l-play" type="button"><i data-i="play"></i>Start</button>
    </div>
    <div class="clock"><span class="date" id="l-date">Ready</span><span class="live" id="l-live"><i></i><span>Paused</span></span>
      <span class="pill calm" id="l-status" style="margin-left:auto"></span></div>
    <div class="prog"><i id="l-prog"></i></div>
    <div class="lv">
      <div>
        <div class="card endbox" id="l-end"></div>
        <div class="kpis">
          <div class="card kpi"><div class="k"><i data-i="up"></i>Account value</div><div class="v" id="k-eq">–</div><div class="s" id="k-eq2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="down"></i>Drawdown now</div><div class="v" id="k-dd">–</div><div class="s" id="k-dd2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="pie"></i>Invested</div><div class="v" id="k-x">–</div><div class="s" id="k-x2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="wave"></i>Volatility, last 20 days</div><div class="v" id="k-vol">–</div><div class="s" id="k-vol2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="shield"></i>1-day VaR 95%</div><div class="v" id="k-var">–</div><div class="s" id="k-var2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="bell"></i>Breaker trips</div><div class="v" id="k-trips">0</div><div class="s" id="k-trips2">Circuit breaker fired</div></div>
        </div>
        <div class="card chartcard"><h4>Account value <span class="lg"><i style="background:var(--base)"></i>No risk rules</span>
          <span class="lg"><i style="background:var(--accent)"></i>With risk rules</span></h4><canvas id="c-eq" height="220"></canvas></div>
        <div class="card chartcard"><h4>Drawdown from peak</h4><canvas id="c-dd" height="170"></canvas></div>
      </div>
      <div class="card alerts"><h4><i data-i="bell"></i>Alerts<span class="count" id="a-count">0</span></h4>
        <ul class="feed" id="feed"><li class="info" data-idle><span class="ai"><i data-i="info"></i></span><div><b>Monitor idle</b><span>Press Start to replay history day by day.</span></div></li></ul></div>
    </div>
  </section>

  <section class="page" id="p-history">
    <div class="card scroll"><table><thead><tr><th>Saved</th><th>Stock</th><th>Strategy</th><th>Risk rules</th><th>Period</th>
      <th class="num">Annual return</th><th class="num">Max drawdown</th><th class="num">Sharpe</th><th></th></tr></thead>
      <tbody id="hist"><tr><td colspan="9" class="empty">Loading…</td></tr></tbody></table></div>
  </section>
</div></div>

<script>
const ICONS=__ICONS__;
const svg=(n,c='ic')=>`<svg class="${c}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n]||''}</svg>`;
function paintIcons(root=document){root.querySelectorAll('i[data-i]').forEach(e=>{e.outerHTML=svg(e.dataset.i)})}
paintIcons();
const $=id=>document.getElementById(id);
const pct=(x,d=1)=>x==null?'–':((x*100).toFixed(d)+'%').replace('-','−');
const loss=(x,d=1)=>x==null?'–':(x>0?'−':'')+(Math.abs(x)*100).toFixed(d)+'%';
const money=x=>'$'+Math.round(x).toLocaleString();
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(path,opt){const r=await fetch(path,opt);const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
const STATUS={calm:['check','Calm'],elevated:['alert','Elevated'],stressed:['alert','Stressed'],halted:['alert','Halted by breaker']};
function pill(el,s){el.className='pill '+s;el.innerHTML=svg(STATUS[s][0])+STATUS[s][1]}
const store={get(k,d){try{return JSON.parse(localStorage.getItem('rl.'+k))??d}catch(e){return d}},set(k,v){try{localStorage.setItem('rl.'+k,JSON.stringify(v))}catch(e){}}};

/* ---------------- router ---------------- */
const PAGES={overview:['Overview','Risk snapshot of every stock you have downloaded.'],
 analyze:['Analyze','Test a strategy with and without risk rules.'],
 live:['Live monitor','Replays history through the engine day by day, like a risk desk screen.'],
 history:['History','Every analysis saved on this server.']};
function route(){const p=(location.hash.match(/#\/(\w+)/)||[])[1]||'overview';const k=PAGES[p]?p:'overview';
 document.querySelectorAll('.page').forEach(e=>e.classList.toggle('on',e.id==='p-'+k));
 document.querySelectorAll('.nav a').forEach(a=>{const on=a.dataset.p===k;a.classList.toggle('on',on);on?a.setAttribute('aria-current','page'):a.removeAttribute('aria-current')});
 $('ptitle').textContent=PAGES[k][0];$('psub').textContent=PAGES[k][1];
 if(k==='history')loadHistory(); if(k==='live')liveCfgChip(); if(k==='overview')loadOverview();}
window.addEventListener('hashchange',route);

/* ---------------- health ---------------- */
async function health(){try{const h=await api('/health');$('hdot').className='dot';$('hstat').textContent='Engine online';
 $('hver').textContent=h.engine_version;$('hcount').textContent=`${h.runs_computed} runs computed, ${h.cache_hits} served from store`}
 catch(e){$('hdot').className='dot off';$('hstat').textContent='Engine offline'}}
setInterval(health,8000);

/* ---------------- overview ---------------- */
let DATASETS={};
function spark(v,status){if(!v.length)return'';const w=280,h=56,lo=Math.min(...v),hi=Math.max(...v)||1;
 const pts=v.map((x,i)=>`${(i/(v.length-1)*w).toFixed(1)},${(h-3-(x-lo)/((hi-lo)||1)*(h-6)).toFixed(1)}`).join(' ');
 const c=status==='calm'?'var(--good)':status==='elevated'?'var(--warn)':'var(--bad)';
 return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`}
async function loadOverview(){
 try{const cards=await api('/api/overview');
  if(!cards.length){$('cards').innerHTML=`<div class="card empty">No data yet. In a terminal run <code>python scripts/fetch_data.py SPY AAPL</code>, then restart the server.</div>`;return}
  $('cards').innerHTML=cards.map(c=>`<article class="card tk">
   <div class="tk-h"><div><div class="sym">${esc(c.symbol)}</div><div class="px">$${c.price.toFixed(2)} · ${esc(c.last)}</div></div>
   <span class="pill ${c.status}">${svg(STATUS[c.status][0])}${STATUS[c.status][1]}</span></div>
   ${spark(c.spark,c.status)}
   <div class="stats"><div><span>Drawdown now</span><b>${loss(c.drawdown_now)}</b></div><div><span>1-year return</span><b>${pct(c.ret_1y)}</b></div>
   <div><span>1-year volatility</span><b>${pct(c.vol_1y)}</b></div><div><span>1-day VaR 95%</span><b>${loss(c.var_95,2)}</b></div>
   <div><span>Worst fall ever</span><b>${loss(c.max_drawdown)}</b></div><div><span>Since</span><b>${esc(c.first.slice(0,4))}</b></div></div>
   <div class="tk-a"><a class="btn" href="#/analyze" data-sym="${esc(c.symbol)}">${svg('sliders')}Analyze</a>
   <a class="btn" href="#/live" data-sym="${esc(c.symbol)}">${svg('pulse')}Live replay</a></div></article>`).join('');
  $('cards').querySelectorAll('a[data-sym]').forEach(a=>a.addEventListener('click',()=>{setSym(a.dataset.sym)}));
 }catch(e){$('cards').innerHTML=`<div class="card empty">Could not load: ${esc(e.message)}</div>`}}

/* ---------------- form ---------------- */
const F=store.get('form',{sym:null,start:'',end:'',strat:'sma_cross',fast:50,slow:200,look:126,volOn:true,vol:15,ddOn:true,dd:15,cool:20,cap:100,fee:1,slip:2,luck:'1000'});
function setSym(s){F.sym=s;$('f-sym').value=s;$('l-sym').value=s;store.set('form',F)}
function readForm(){Object.assign(F,{sym:$('f-sym').value,start:$('f-start').value,end:$('f-end').value,fast:+$('f-fast').value,slow:+$('f-slow').value,
 look:+$('f-look').value,volOn:$('f-vol-on').checked,vol:+$('f-vol').value,ddOn:$('f-dd-on').checked,dd:+$('f-dd').value,cool:+$('f-cool').value,
 cap:+$('f-cap').value,fee:+$('f-fee').value,slip:+$('f-slip').value,luck:$('f-luck').value});store.set('form',F);paintForm()}
function paintForm(){document.querySelectorAll('#f-strat button').forEach(b=>b.classList.toggle('on',b.dataset.v===F.strat));
 $('p-sma').style.display=F.strat==='sma_cross'?'':'none';$('p-mom').style.display=F.strat==='momentum'?'':'none';
 $('v-vol').textContent=F.vol+'%';$('v-dd').textContent=F.dd+'%';$('v-cool').textContent=F.cool+' days';$('v-cap').textContent=F.cap+'%';
 $('r-vol').classList.toggle('off',!F.volOn);$('r-dd').classList.toggle('off',!F.ddOn);$('r-cool').classList.toggle('off',!F.ddOn)}
function fillForm(){$('f-start').value=F.start;$('f-end').value=F.end;$('f-fast').value=F.fast;$('f-slow').value=F.slow;$('f-look').value=F.look;
 $('f-vol-on').checked=F.volOn;$('f-vol').value=F.vol;$('f-dd-on').checked=F.ddOn;$('f-dd').value=F.dd;$('f-cool').value=F.cool;$('f-cap').value=F.cap;
 $('f-fee').value=F.fee;$('f-slip').value=F.slip;$('f-luck').value=F.luck;paintForm()}
function cfg(withRules){const params=F.strat==='sma_cross'?{fast:F.fast,slow:F.slow}:F.strat==='momentum'?{lookback:F.look}:{};
 const c={symbol:F.sym,start:F.start||null,end:F.end||null,strategy:{name:F.strat,params},
  costs:{fee_bps:F.fee,slippage_bps:F.slip},luck_test:{trials:+F.luck}};
 if(withRules)c.risk={vol_target:F.volOn?F.vol/100:null,max_drawdown:F.ddOn?F.dd/100:null,cooldown_days:F.cool,max_exposure:F.cap/100};
 return c}
const hasRules=()=>F.volOn||F.ddOn||F.cap<100;
document.querySelectorAll('#form input,#form select').forEach(e=>e.addEventListener('input',readForm));
document.querySelectorAll('#f-strat button').forEach(b=>b.addEventListener('click',()=>{F.strat=b.dataset.v;readForm()}));
$('run').addEventListener('click',async()=>{readForm();const b=$('run');b.disabled=true;$('amsg').className='msg';
 $('amsg').textContent='Running…';const t0=performance.now();
 try{const ids=[];let cached=true;
  for(const c of hasRules()?[cfg(false),cfg(true)]:[cfg(false)]){const r=await api('/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});ids.push(r.run_id);cached=cached&&r.cached}
  $('result').innerHTML=`<iframe title="Risk report" src="/report?runs=${ids.join(',')}&embed=1"></iframe>`;
  $('amsg').innerHTML=`${cached?'Loaded from saved results':'Computed'} in ${Math.round(performance.now()-t0)} ms. <a href="/report?runs=${ids.join(',')}" target="_blank" rel="noopener">Open full page</a>`;
  health();
 }catch(e){$('amsg').className='msg err';$('amsg').textContent=e.message}finally{b.disabled=false}});

/* ---------------- live monitor ---------------- */
const L={es:null,playing:false,t:0,days:0,data:[],trips:0,alerts:0,ended:false,flags:{}};
function liveCfgChip(){const s=F.strat==='sma_cross'?`Trend ${F.fast}/${F.slow}`:F.strat==='momentum'?`Momentum ${F.look}d`:'Buy & hold';
 const r=[F.volOn?`vol target ${F.vol}%`:null,F.ddOn?`breaker ${F.dd}%`:null,F.cap<100?`cap ${F.cap}%`:null].filter(Boolean).join(', ')||'no risk rules';
 $('l-cfg').innerHTML=`${esc(s)} · ${esc(r)} · <a href="#/analyze">change</a>`}
function addAlert(level,icon,title,text){L.alerts++;$('a-count').textContent=L.alerts;
 const li=document.createElement('li');li.className=level;li.innerHTML=`<span class="ai">${svg(icon)}</span><div><b>${esc(title)}</b><span>${esc(text)}</span></div>`;
 const feed=$('feed');if(feed.firstElementChild&&feed.firstElementChild.dataset.idle!==undefined)feed.innerHTML='';feed.prepend(li);while(feed.children.length>80)feed.lastChild.remove()}
function setPlaying(p){L.playing=p;$('l-play').innerHTML=svg(p?'pause':'play')+(p?'Pause':(L.t>0&&!L.ended?'Resume':'Start'));
 const lv=$('l-live');lv.classList.toggle('on',p);lv.querySelector('span').textContent=p?'LIVE':(L.ended?'Finished':'Paused')}
function stop(){if(L.es){L.es.close();L.es=null}}
function reset(){stop();Object.assign(L,{t:0,days:0,data:[],trips:0,alerts:0,ended:false,flags:{}});$('feed').innerHTML='';$('a-count').textContent='0';
 $('l-end').classList.remove('on');$('k-trips').textContent='0';$('l-prog').style.width='0';draw()}
function start(from){stop();readForm();L.ended=false;$('l-end').classList.remove('on');
 const q=`/api/live?cfg=${encodeURIComponent(JSON.stringify(cfg(true)))}&speed=${$('l-speed').value}&from=${from}`;
 const es=new EventSource(q);L.es=es;setPlaying(true);
 es.addEventListener('init',e=>{const m=JSON.parse(e.data);L.days=m.days;
   if(from===0)addAlert('info','pulse',`Replay started: ${m.symbol}`,`${m.first} to ${m.last}, ${m.days.toLocaleString()} trading days`)});
 es.addEventListener('day',e=>onDay(JSON.parse(e.data)));
 es.addEventListener('end',e=>{const s=JSON.parse(e.data);L.ended=true;stop();setPlaying(false);showEnd(s);
   if($('l-loop').checked)setTimeout(()=>{if(L.ended&&!L.playing){reset();start(0)}},2500)});
 es.onerror=()=>{if(!L.ended){stop();setPlaying(false);addAlert('crit','alert','Connection lost','Is the RiskLens server still running?')}}}
function onDay(d){L.t=d.t;L.data.push(d);$('l-date').textContent=d.d;$('l-prog').style.width=(L.days?(d.t+1)/L.days*100:0)+'%';
 $('k-eq').textContent=money(d.eq[1]);$('k-eq2').innerHTML=`No rules: <b>${money(d.eq[0])}</b>`;
 $('k-dd').textContent=loss(d.dd[1]);$('k-dd2').innerHTML=`No rules: <b>${loss(d.dd[0])}</b>`;
 $('k-x').textContent=pct(d.x[1],0);$('k-x2').innerHTML=`No rules: <b>${pct(d.x[0],0)}</b>`;
 $('k-vol').textContent=pct(d.vol[1]);$('k-vol2').innerHTML=`No rules: <b>${pct(d.vol[0])}</b>`;
 $('k-var').textContent=loss(d.var[1],2);$('k-var2').innerHTML=`No rules: <b>${loss(d.var[0],2)}</b>`;
 for(const [k,txt] of d.ev){if(k==='DRAWDOWN_BREAKER'){L.trips++;$('k-trips').textContent=L.trips;L.flags.halt=true;addAlert('crit','alert',`Circuit breaker fired · ${d.d}`,txt)}
  else if(k==='RESUME'){L.flags.halt=false;addAlert('ok','check',`Trading resumed · ${d.d}`,txt)}
  else addAlert('info','info',`${k} · ${d.d}`,txt)}
 // monitor alerts: threshold crossings (raised once, re-armed when back below)
 const cross=(key,val,lim,level,icon,title,text)=>{if(val!=null&&val>=lim&&!L.flags[key]){L.flags[key]=1;addAlert(level,icon,title,text)}
  else if(val!=null&&val<lim*0.8)L.flags[key]=0};
 cross('vol30',d.vol[0],0.30,'warn','wave',`Volatility spike · ${d.d}`,`Market 20-day volatility reached ${pct(d.vol[0])}`);
 cross('dd10',d.dd[0],0.10,'warn','down',`Unprotected drawdown passed 10% · ${d.d}`,`Without rules the account is ${loss(d.dd[0])} below its peak`);
 cross('dd20',d.dd[0],0.20,'crit','down',`Unprotected drawdown passed 20% · ${d.d}`,`With rules: ${loss(d.dd[1])}`);
 const st=L.flags.halt?'halted':d.dd[1]>=0.15?'stressed':d.dd[1]>=0.05?'elevated':'calm';pill($('l-status'),st);
 schedule()}
function showEnd(s){const b=s.base,m=s.managed;const el=$('l-end');el.classList.add('on');
 el.innerHTML=`<b>Replay finished.</b> Worst fall ${loss(b.max_drawdown)} without rules vs <b>${loss(m.max_drawdown)}</b> with rules · volatility ${pct(b.volatility)} vs <b>${pct(m.volatility)}</b> · annual return ${pct(b.cagr)} vs <b>${pct(m.cagr)}</b>.`;
 addAlert('ok','check','Replay finished',`Breaker fired ${L.trips} time${L.trips===1?'':'s'}`)}
let raf=0;function schedule(){if(!raf)raf=requestAnimationFrame(()=>{raf=0;draw()})}
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()}
function plot(cv,series,opts){const dpr=window.devicePixelRatio||1,w=cv.clientWidth,h=+cv.getAttribute('height');
 cv.width=w*dpr;cv.height=h*dpr;cv.style.height=h+'px';const g=cv.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,w,h);
 const n=L.days||1,pl=58,pr=8,pt=8,pb=22,pw=w-pl-pr,ph=h-pt-pb;
 let lo=opts.lo,hi=opts.hi;for(const s of series)for(const v of s){if(v<lo)lo=v;if(v>hi)hi=v}if(!isFinite(lo)||!isFinite(hi)){lo=0;hi=1}if(hi===lo)hi=lo+1;
 const raw=(hi-lo)/4,mag=Math.pow(10,Math.floor(Math.log10(raw))),step=[1,2,2.5,5,10].map(m=>m*mag).find(x=>x>=raw);
 lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
 const Y=v=>pt+ph*(1-(v-lo)/(hi-lo)),X=i=>pl+pw*i/Math.max(1,n-1);
 g.font='12px "IBM Plex Sans",system-ui,sans-serif';g.fillStyle=css('--ink3');g.strokeStyle=css('--grid');g.lineWidth=1;
 for(let v=lo;v<=hi+step*1e-6;v+=step){const y=Y(v);g.beginPath();g.moveTo(pl,y);g.lineTo(w-pr,y);g.stroke();g.textAlign='right';g.fillText(opts.fmt(v),pl-8,y+4)}
 g.textAlign='center';let last='';L.data.forEach((d,i)=>{const yr=d.d.slice(0,4);if(yr!==last){if(last)g.fillText(yr,X(d.t),h-6);last=yr}});
 const cols=[css('--base'),css('--accent')];
 series.forEach((s,k)=>{if(!s.length)return;g.beginPath();s.forEach((v,i)=>{const x=X(L.data[i].t),y=Y(v);i?g.lineTo(x,y):g.moveTo(x,y)});
  if(opts.fill){g.lineTo(X(L.data[s.length-1].t),Y(0));g.lineTo(X(L.data[0].t),Y(0));g.closePath();g.globalAlpha=.14;g.fillStyle=cols[k];g.fill();g.globalAlpha=1;
   g.beginPath();s.forEach((v,i)=>{const x=X(L.data[i].t),y=Y(v);i?g.lineTo(x,y):g.moveTo(x,y)})}
  g.strokeStyle=cols[k];g.lineWidth=2;g.lineJoin='round';g.stroke()});
 g.fillStyle=css('--warn');L.data.forEach(d=>{for(const [k] of d.ev)if(k==='DRAWDOWN_BREAKER'){const x=X(d.t);g.beginPath();g.moveTo(x-5,pt);g.lineTo(x+5,pt);g.lineTo(x,pt+8);g.fill()}})}
function draw(){plot($('c-eq'),[L.data.map(d=>d.eq[0]),L.data.map(d=>d.eq[1])],{lo:Infinity,hi:-Infinity,fmt:v=>'$'+Math.round(v/1000)+'k'});
 plot($('c-dd'),[L.data.map(d=>-d.dd[0]),L.data.map(d=>-d.dd[1])],{lo:0,hi:0,fill:true,fmt:v=>Math.abs(v)<1e-9?'0%':(v*100).toFixed(0).replace('-','−')+'%'})}
window.addEventListener('resize',schedule);
$('l-play').addEventListener('click',()=>{if(L.playing){stop();setPlaying(false)}else{if(L.ended)reset();start(L.data.length?L.t+1:0)}});
$('l-restart').addEventListener('click',()=>{reset();start(0)});
$('l-speed').addEventListener('change',()=>{if(L.playing)start(L.t+1)});
$('l-sym').addEventListener('change',()=>{setSym($('l-sym').value);reset();setPlaying(false);$('l-date').textContent='Ready'});

/* ---------------- history ---------------- */
async function loadHistory(){try{const rows=await api('/api/history');
 $('hist').innerHTML=rows.length?rows.map(r=>{const s=r.strategy,p=Object.values(s.params).join('/');const k=r.risk;
  const rules=[k.vol_target?`vol ${Math.round(k.vol_target*100)}%`:null,k.max_drawdown?`breaker ${Math.round(k.max_drawdown*100)}%`:null,k.max_exposure<1?`cap ${Math.round(k.max_exposure*100)}%`:null].filter(Boolean).join(', ')||'none';
  return `<tr><td>${esc(r.saved)}</td><td><b>${esc(r.symbol)}</b></td><td>${esc({sma_cross:'Trend',momentum:'Momentum',buy_and_hold:'Buy & hold'}[s.name]||s.name)}${p?' '+esc(p):''}</td><td>${esc(rules)}</td>
  <td>${esc(r.start.slice(0,7))} to ${esc(r.end.slice(0,7))}</td><td class="num">${pct(r.cagr)}</td><td class="num">${loss(r.max_drawdown)}</td><td class="num">${r.sharpe.toFixed(2)}</td>
  <td><a class="btn" style="padding:6px 8px" href="/report?runs=${r.run_id}" target="_blank" rel="noopener" aria-label="Open report" title="Open report">${svg('external')}</a></td></tr>`}).join('')
  :'<tr><td colspan="9" class="empty">No analyses yet. Run one from Analyze.</td></tr>'}
 catch(e){$('hist').innerHTML=`<tr><td colspan="9" class="empty">${esc(e.message)}</td></tr>`}}

/* ---------------- boot ---------------- */
(async()=>{try{DATASETS=await api('/datasets')}catch(e){DATASETS={}}
 const syms=Object.keys(DATASETS);const opts=syms.map(s=>`<option>${esc(s)}</option>`).join('');$('f-sym').innerHTML=opts;$('l-sym').innerHTML=opts;
 if(!F.sym||!syms.includes(F.sym))F.sym=syms.includes('SPY')?'SPY':syms[0]||null;
 if(F.sym)setSym(F.sym);fillForm();liveCfgChip();health();route();draw()})();
</script></body></html>"""


def page(version: str) -> str:
    return PAGE.replace("__ICONS__", _icons_json()).replace("__VERSION__", version)
