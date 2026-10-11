"""The RiskLens web app, served by the run server at http://127.0.0.1:8765/

One self-contained page (no build step, no external JS) with six screens:
  Overview      risk snapshot of every downloaded dataset (watchlist cards)
  Live market   real prices as they happen (Coinbase real-time for crypto,
                Yahoo delayed for stocks) with live risk feedback, alerts and
                a measured latency breakdown (Server-Sent Events)
  Risk gateway  pre-trade risk checks on a paper account: every order is approved
                or rejected against live prices and limits; kill switch
  Analyze       controls for stock, strategy and risk rules; runs and shows the report
  Replay        replays history through the engine day by day, streaming risk
                metrics and alerts like a risk desk monitor
  History       every stored run, reopenable
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
        "radio": '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5"/>',
        "timer": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M9 2h6"/>',
        "range": '<path d="M4 7h16M4 17h16M8 4v6M16 14v6"/>',
        "gate": '<path d="M4 21V9l8-6 8 6v12"/><path d="M9 21v-7h6v7"/>',
        "power": '<path d="M12 3v9"/><path d="M6.4 7.5a8 8 0 1 0 11.2 0"/>',
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
.tk-a .btn.up{flex:0 0 auto;padding:7px 10px}
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
.row2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
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
/* live market */
.symin{border:1px solid var(--rule);border-radius:8px;padding:7px 10px;background:var(--panel);width:128px;text-transform:uppercase;font-weight:600}
.qchips{display:flex;gap:6px;flex-wrap:wrap}
.qchip{border:1px solid var(--rule);background:var(--soft);border-radius:999px;padding:4px 11px;font-size:13px;cursor:pointer}
.qchip:hover{border-color:var(--accent);color:var(--accent)}
.lv-bar input[type=search]::-webkit-search-cancel-button{display:none}
.mpx{font-size:30px;font-weight:600;border-radius:8px;padding:0 6px}
.mpx.up{animation:fup .9s}.mpx.dn{animation:fdn .9s}
@keyframes fup{from{background:var(--goodbg);color:var(--good)}}@keyframes fdn{from{background:var(--badbg);color:var(--bad)}}
.mchg{font-size:14px;font-weight:600;color:var(--ink2)}.mchg.pos{color:var(--good)}.mchg.neg{color:var(--bad)}
.clock .live.wait{color:var(--warn)}.clock .live.wait i{background:var(--warn);animation:pulse .8s infinite}
.clock .live.stale{color:var(--warn)}.clock .live.stale i{background:var(--warn)}
.srcline{margin:-6px 0 16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--ink2)}
.tag{font-size:12px;font-weight:600;border-radius:6px;padding:2px 8px;background:var(--accent2);color:var(--accent)}
.tag.sim,.tag.delay{background:var(--warnbg);color:var(--warn)}
.side{position:sticky;top:16px;display:flex;flex-direction:column;gap:16px;max-height:calc(100vh - 32px)}
.side .alerts{position:static;min-height:0;flex:1;max-height:none}
.lat{padding:16px}
.lat h4{margin:0 0 10px;font-size:15px;display:flex;align-items:center;gap:8px}
.lat dl{display:grid;grid-template-columns:1fr auto;gap:7px 12px;margin:0;font-size:14px}
.lat dt{color:var(--ink2)}.lat dd{margin:0;font-weight:600;text-align:right}
.lat .tot{border-top:1px solid var(--rule);padding-top:8px;font-size:16px}
.lat p{font-size:12px;color:var(--ink3);margin:10px 0 0;line-height:1.45}
.kpi .v.sm{font-size:18px;padding:3px 0 2px}
/* stock bar: one-click switching between stocks */
.symbar{display:none;flex-direction:column;gap:10px;padding:12px 32px 0}
.symbar.on{display:flex}
.symtop{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.search{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--rule);border-radius:12px;
padding:4px 4px 4px 12px;flex:0 1 600px;min-width:0}
.search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent2)}
.search .ic{width:18px;height:18px;color:var(--ink3)}
.search input{border:0;outline:0;background:transparent;flex:1;min-width:0;font-size:15px;font-weight:600;text-transform:uppercase;padding:6px 0}
.search input::placeholder{text-transform:none;font-weight:400;color:var(--ink3)}
.search select{border:0;background:var(--soft);border-radius:8px;padding:6px 8px;font-size:13px;color:var(--ink2)}
.search .btn{padding:7px 16px}
.symrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.symbar .lab{font-size:13px;color:var(--ink3);font-weight:600;margin-right:2px}
.symchips{display:flex;gap:6px;flex-wrap:wrap}
.symchip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--rule);background:var(--panel);border-radius:999px;
padding:5px 13px;font-weight:600;font-size:14px;cursor:pointer;color:var(--ink)}
.symchip:hover{border-color:var(--accent);color:var(--accent)}
.symchip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.sd{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.sd.calm{background:var(--good)}.sd.elevated{background:var(--warn)}.sd.stressed,.sd.halted{background:var(--bad)}
.symchip.on .sd{box-shadow:0 0 0 2px #fff}
.symmsg{font-size:13px;color:var(--ink2)}.symmsg.err{color:var(--bad)}.symmsg.ok{color:var(--good)}
.symhint{margin-left:auto;font-size:12px;color:var(--ink3)}
.symhint kbd{font:inherit;border:1px solid var(--rule);border-bottom-width:2px;border-radius:5px;padding:0 5px;background:var(--panel)}
.result.busy iframe{opacity:.35;transition:opacity .2s}
.periods{display:flex;gap:6px;flex-wrap:wrap;margin:-2px 0 4px}
.qchip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.presets{margin-bottom:10px}
@media (max-width:760px){.symbar{padding:10px 14px 0}.symhint{display:none}}
/* risk gateway */
.gw-kill{display:none;align-items:center;gap:14px;padding:14px 18px;margin-bottom:16px;background:var(--badbg);border-color:var(--bad);color:var(--bad)}
.gw-kill.on{display:flex}.gw-kill b{display:block;font-size:16px}.gw-kill span{color:var(--ink2)}
.gw-kill .ic{width:28px;height:28px;flex:none}
.btn.kill{background:var(--bad);border-color:var(--bad);color:#fff}.btn.kill:hover{filter:brightness(1.1);color:#fff}
.gw{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;align-items:start}
.gcard{padding:18px 20px;margin-bottom:16px}
.gcard h4{margin:0 0 12px;font-size:15px;display:flex;align-items:center;gap:8px}
.gcard h4 .ic{width:18px;height:18px;color:var(--ink3)}
.gcard h4 .r{margin-left:auto;font-weight:400;font-size:13px;color:var(--ink3)}
.ticket{display:grid;grid-template-columns:150px 1fr auto;gap:12px;align-items:end}
.ticket .seg{grid-template-columns:1fr 1fr}
.seg button.buy.on{background:var(--good)}.seg button.sell.on{background:var(--bad)}
.ticket input{border:1px solid var(--rule);border-radius:8px;padding:7px 10px;background:var(--panel);width:100%}
.ticket .est{font-size:13px;color:var(--ink3);margin-top:4px;min-height:18px}
.quick{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
.chk{list-style:none;margin:0;padding:0}
.chk li{display:grid;grid-template-columns:26px 150px 1fr;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid var(--grid);font-size:14px}
.chk li .ic{width:20px;height:20px}.chk li.ok .ic{color:var(--good)}.chk li.no .ic{color:var(--bad)}
.chk li.no{background:var(--badbg)}.chk b{font-weight:600}.chk span{color:var(--ink2)}
.verd{display:inline-flex;align-items:center;gap:6px;font-weight:600;border-radius:999px;padding:3px 12px;font-size:13px}
.verd.ok{background:var(--goodbg);color:var(--good)}.verd.no{background:var(--badbg);color:var(--bad)}
.acct .big{font-size:28px;font-weight:600}.acct .pnl{font-size:14px;font-weight:600}
.acct .pnl.pos{color:var(--good)}.acct .pnl.neg{color:var(--bad)}
.acct dl{display:grid;grid-template-columns:1fr auto;gap:6px 12px;margin:12px 0 0;font-size:14px}
.acct dt{color:var(--ink2)}.acct dd{margin:0;font-weight:600;text-align:right}
.meter{margin-top:14px}.meter .lab{display:flex;justify-content:space-between;font-size:13px;color:var(--ink2)}
.meter .bar{position:relative;height:10px;background:var(--grid);border-radius:5px;margin-top:5px;overflow:visible}
.meter .fillb{position:absolute;left:0;top:0;bottom:0;border-radius:5px;background:var(--accent)}
.meter .fillb.hot{background:var(--bad)}
.meter .lim{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink)}
.recon{margin-top:14px;font-size:13px;color:var(--good);display:flex;gap:6px;align-items:center}
.recon .ic{width:16px;height:16px}.recon.bad{color:var(--bad)}
.lims .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.lims .f input{border:1px solid var(--rule);border-radius:8px;padding:6px 8px;background:var(--panel);width:100%}
.res{display:inline-block;font-size:12px;font-weight:600;border-radius:999px;padding:1px 9px}
.res.ok{background:var(--goodbg);color:var(--good)}.res.no{background:var(--badbg);color:var(--bad)}.res.fl{background:var(--warnbg);color:var(--warn)}
#g-log td{font-size:13px}#g-log td.why{white-space:normal;color:var(--ink2);min-width:170px}
@media (max-width:1100px){.gw{grid-template-columns:1fr}.ticket{grid-template-columns:1fr}}
/* history */
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--rule);font-size:14px;white-space:nowrap}
thead th{font-size:12px;color:var(--ink2);font-weight:600;background:var(--soft)}
td.num,th.num{text-align:right}
.scroll{overflow-x:auto}
@media (max-width:1100px){.lv{grid-template-columns:1fr} .alerts{position:static;max-height:420px} .side{position:static;max-height:none} .side .alerts{max-height:420px}}
@media (max-width:900px){.an{grid-template-columns:1fr} .form{position:static} .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.shell{display:block} .nav{position:sticky;height:auto;flex-direction:row;align-items:center;padding:8px;z-index:9;overflow-x:auto}
.brand{padding:4px 8px} .brand span,.nav a span,.navfoot{display:none} .top,.page{padding-left:14px;padding-right:14px}}
</style></head><body>
<div class="shell">
<aside class="nav" aria-label="Main">
  <div class="brand"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/></svg><span>RiskLens</span></div>
  <a href="#/overview" data-p="overview"><i data-i="grid"></i><span>Overview</span></a>
  <a href="#/market" data-p="market"><i data-i="radio"></i><span>Live market</span></a>
  <a href="#/gateway" data-p="gateway"><i data-i="gate"></i><span>Risk gateway</span></a>
  <a href="#/analyze" data-p="analyze"><i data-i="sliders"></i><span>Analyze</span></a>
  <a href="#/replay" data-p="replay"><i data-i="pulse"></i><span>Replay</span></a>
  <a href="#/history" data-p="history"><i data-i="clock"></i><span>History</span></a>
  <div class="navfoot"><span class="dot" id="hdot"></span><b id="hstat">Engine online</b><br>
  engine <span id="hver">__VERSION__</span><br><span id="hcount"></span><br><br>Simulated results, no real orders. Not investment advice.</div>
</aside>
<div class="main">
  <header class="top"><div><h1 id="ptitle">Overview</h1><p id="psub"></p></div></header>
  <div class="symbar" id="symbar">
    <div class="symtop">
      <form class="search" id="symsearch" role="search" onsubmit="return false">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input id="add-sym" list="tickers" maxlength="15" placeholder="Analyze any stock: type a ticker, e.g. TSLA" aria-label="Ticker to analyze" autocomplete="off" spellcheck="false">
        <select id="add-from" aria-label="How much history to download" title="How much history to download the first time">
          <option value="2000-01-01">since 2000</option><option value="2005-01-01" selected>since 2005</option>
          <option value="2010-01-01">since 2010</option><option value="2015-01-01">since 2015</option></select>
        <button class="btn primary" id="add-go" type="submit">Analyze</button>
      </form>
      <span class="symmsg" id="symmsg" role="status"></span>
    </div>
    <datalist id="tickers"><option value="SPY">S&amp;P 500 fund</option><option value="QQQ">Nasdaq-100 fund</option><option value="AAPL">Apple</option>
      <option value="MSFT">Microsoft</option><option value="NVDA">Nvidia</option><option value="AMZN">Amazon</option><option value="GOOGL">Alphabet</option>
      <option value="META">Meta</option><option value="TSLA">Tesla</option><option value="BRK-B">Berkshire Hathaway</option><option value="JPM">JPMorgan Chase</option>
      <option value="XOM">Exxon Mobil</option><option value="GLD">Gold fund</option><option value="TLT">Long-term Treasury fund</option>
      <option value="^GSPC">S&amp;P 500 index</option><option value="^IXIC">Nasdaq index</option><option value="BTC-USD">Bitcoin</option></datalist>
    <div class="symrow"><span class="lab">Your stocks</span><div class="symchips" id="symchips"></div>
      <span class="symhint" id="symhint"><kbd>/</kbd> search · <kbd>←</kbd> <kbd>→</kbd> switch</span></div>
  </div>

  <section class="page" id="p-overview">
    <div class="cards" id="cards"><div class="card empty">Loading datasets…</div></div>
  </section>

  <section class="page" id="p-market">
    <div class="card lv-bar">
      <input class="symin" id="m-sym" type="search" list="m-list" maxlength="15" aria-label="Symbol" placeholder="BTC-USD" autocomplete="off" spellcheck="false">
      <datalist id="m-list"><option>BTC-USD</option><option>ETH-USD</option><option>SOL-USD</option><option>DOGE-USD</option><option>SPY</option><option>AAPL</option><option>QQQ</option></datalist>
      <div class="qchips" id="m-quick"></div>
      <select id="m-src" aria-label="Data source"><option value="auto">Auto source</option><option value="coinbase">Coinbase · real-time crypto</option>
        <option value="yahoo">Yahoo · stocks, delayed</option><option value="sim">Simulated · offline</option></select>
      <span class="chip" id="m-cfg"></span>
      <span class="grow"></span>
      <button class="btn primary" id="m-go" type="button"><i data-i="play"></i>Connect</button>
    </div>
    <div class="clock"><span class="date" id="m-symbig">BTC-USD</span><span class="mpx" id="m-px">–</span><span class="mchg" id="m-chg"></span>
      <span class="live" id="m-live"><i></i><span>Offline</span></span>
      <span class="pill calm" id="m-brk" style="margin-left:auto;display:none"></span></div>
    <div class="srcline" id="m-srcline">Press Connect. Crypto pairs stream trade by trade from Coinbase, around the clock.</div>
    <div class="lv">
      <div>
        <div class="kpis">
          <div class="card kpi"><div class="k"><i data-i="pie"></i>Rules allow you to hold</div><div class="v" id="m-x">–</div><div class="s" id="m-x2">Volatility targeting, right now</div></div>
          <div class="card kpi"><div class="k"><i data-i="down"></i>Below session high</div><div class="v" id="m-dd">–</div><div class="s" id="m-dd2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="wave"></i>Volatility, last 60 min</div><div class="v" id="m-vol">–</div><div class="s" id="m-vol2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="shield"></i>1-minute VaR 95%</div><div class="v" id="m-var">–</div><div class="s" id="m-var2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="range"></i>Session range</div><div class="v sm" id="m-rng">–</div><div class="s" id="m-rng2"></div></div>
          <div class="card kpi"><div class="k"><i data-i="bolt"></i>Feed activity</div><div class="v" id="m-act">–</div><div class="s" id="m-act2"></div></div>
        </div>
        <div class="card chartcard"><h4>Price, 1-minute bars <span class="lg"><i style="background:var(--accent)"></i>Price</span>
          <span class="lg"><i style="background:var(--base)"></i>Session high</span></h4><canvas id="c-mpx" height="240"></canvas></div>
        <div class="card chartcard"><h4>Drawdown from session high <span class="lg"><i style="background:var(--bad)"></i>Drawdown</span>
          <span class="lg"><i style="background:var(--warn)"></i>Breaker limit</span></h4><canvas id="c-mdd" height="160"></canvas></div>
      </div>
      <div class="side">
        <div class="card lat"><h4><i data-i="timer"></i>Latency, measured</h4>
          <dl><dt>Exchange &rarr; server</dt><dd id="lt-feed">–</dd>
          <dt>Risk calculation</dt><dd id="lt-calc">–</dd>
          <dt>Server &rarr; browser</dt><dd id="lt-push">–</dd>
          <dt class="tot">Trade &rarr; your screen</dt><dd class="tot" id="lt-e2e">–</dd></dl>
          <p id="lt-note">Medians of recent updates.</p></div>
        <div class="card alerts"><h4><i data-i="bell"></i>Alerts<span class="count" id="m-count">0</span></h4>
          <ul class="feed" id="m-feed"><li class="info" data-idle><span class="ai"><i data-i="info"></i></span><div><b>Not connected</b><span>Pick a symbol and press Connect.</span></div></li></ul></div>
      </div>
    </div>
  </section>

  <section class="page" id="p-gateway">
    <div class="card lv-bar">
      <span class="chip" id="g-feed">Not connected to a live price</span>
      <div class="qchips" id="g-quick" aria-label="Switch live price"></div>
      <span class="grow"></span>
      <span class="note" id="g-session"></span>
      <button class="btn kill" id="g-kill" type="button"><i data-i="power"></i>Kill switch</button>
    </div>
    <div class="card gw-kill" id="g-killbox" role="alert"><i data-i="alert"></i><div><b id="g-killt">Kill switch engaged</b>
      <span id="g-killd"></span></div><span class="grow" style="flex:1"></span><button class="btn" id="g-rearm" type="button">Re-arm</button></div>
    <div class="gw">
      <div>
        <div class="card gcard"><h4><i data-i="sliders"></i>New order<span class="r">Paper account: simulated money, no real orders</span></h4>
          <div class="ticket">
            <div class="seg" id="g-side" role="group" aria-label="Side"><button type="button" class="buy on" data-v="buy">Buy</button><button type="button" class="sell" data-v="sell">Sell</button></div>
            <div><label class="note" for="g-qty">Quantity of <b id="g-sym">BTC-USD</b></label><input id="g-qty" type="text" inputmode="decimal" value="0.1" autocomplete="off"><div class="est" id="g-est"></div></div>
            <button class="btn primary" id="g-send" type="button"><i data-i="shield"></i>Check and send</button>
          </div>
          <div class="quick" id="g-quick"><span class="note">Size by share of equity:</span>
            <button class="qchip" type="button" data-p="0.05">5%</button><button class="qchip" type="button" data-p="0.1">10%</button>
            <button class="qchip" type="button" data-p="0.25">25%</button><button class="qchip" type="button" data-p="0.5">50%</button>
            <button class="qchip" type="button" data-p="1">100%</button></div>
        </div>
        <div class="card gcard"><h4><i data-i="check"></i>Pre-trade checks<span class="r" id="g-vtime"></span></h4>
          <div id="g-verdict" class="note">Send an order to see all eight checks run on it.</div>
          <ul class="chk" id="g-checks"></ul></div>
        <div class="card gcard"><h4><i data-i="clock"></i>Decision log<span class="r" id="g-stats"></span></h4>
          <div class="scroll"><table><thead><tr><th>Time</th><th>Order</th><th class="num">Price</th><th class="num">Value</th><th>Result</th><th>Why</th><th class="num">Check</th></tr></thead>
          <tbody id="g-log"><tr><td colspan="7" class="empty">No orders yet.</td></tr></tbody></table></div></div>
      </div>
      <div>
        <div class="card gcard acct"><h4><i data-i="pie"></i>Account</h4>
          <div class="big" id="g-eq">$100,000</div><div class="pnl" id="g-pnl"></div>
          <dl><dt>Cash</dt><dd id="g-cash">–</dd><dt>Holdings</dt><dd id="g-hold">none</dd><dt>Fees paid</dt><dd id="g-fees">–</dd></dl>
          <div class="meter"><div class="lab"><span>Exposure</span><span id="g-exl"></span></div><div class="bar"><i class="fillb" id="g-exb"></i><i class="lim" id="g-exm"></i></div></div>
          <div class="meter"><div class="lab"><span>Below peak</span><span id="g-ddl"></span></div><div class="bar"><i class="fillb" id="g-ddb"></i><i class="lim" id="g-ddm"></i></div></div>
          <div class="recon" id="g-recon"></div>
        </div>
        <div class="card gcard lims"><h4><i data-i="lock"></i>Limits</h4>
          <div class="row3">
            <div class="f"><label for="g-mo">Max order $</label><input id="g-mo" type="number" min="1" step="1000" value="25000"></div>
            <div class="f"><label for="g-mp">Max holding $</label><input id="g-mp" type="number" min="1" step="1000" value="50000"></div>
            <div class="f"><label for="g-rate">Orders / min</label><input id="g-rate" type="number" min="1" max="10000" value="30"></div>
          </div>
          <div class="note" id="g-rules"></div>
          <div class="runbar"><button class="btn" id="g-reset" type="button"><i data-i="restart"></i>Reset account</button></div>
        </div>
      </div>
    </div>
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
        <div class="periods" id="f-period" role="group" aria-label="Period"><button type="button" class="qchip" data-y="0">All</button>
          <button type="button" class="qchip" data-y="10">10 years</button><button type="button" class="qchip" data-y="5">5 years</button>
          <button type="button" class="qchip" data-y="3">3 years</button></div>
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
        <div class="seg presets" id="f-preset" role="group" aria-label="Risk preset"><button type="button" data-p="off">No rules</button>
          <button type="button" data-p="mod">Moderate</button><button type="button" data-p="con">Conservative</button></div>
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
          <a class="btn" id="tolive" href="#/replay" title="Replay these settings live"><i data-i="pulse"></i></a></div>
        <div class="msg" id="amsg" role="status"></div>
      </form>
      <div class="card result" id="result"><div class="placeholder"><i data-i="sliders"></i>
        <b>Set up a strategy and its risk rules, then run the analysis.</b>
        <span>Results compare the same strategy with and without the rules.</span></div></div>
    </div>
  </section>

  <section class="page" id="p-replay">
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
      <th class="num">Annual return</th><th class="num">Max drawdown</th><th class="num">Sharpe</th><th>Report</th></tr></thead>
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
const PAGES={overview:['Overview','Risk snapshot of every stock you have. Type any ticker above to analyze a new one.'],
 market:['Live market','Real prices as they happen, with live risk feedback from your rules.'],
 gateway:['Risk gateway','Every order is checked against live prices and your limits before it can trade.'],
 analyze:['Analyze','Test a strategy with and without risk rules.'],
 replay:['Replay','Replays history through the engine day by day, like a risk desk screen.'],
 history:['History','Every analysis saved on this server.']};
function route(){const p=(location.hash.match(/#\/(\w+)/)||[])[1]||'overview';const k=PAGES[p]?p:'overview';
 document.querySelectorAll('.page').forEach(e=>e.classList.toggle('on',e.id==='p-'+k));
 document.querySelectorAll('.nav a').forEach(a=>{const on=a.dataset.p===k;a.classList.toggle('on',on);on?a.setAttribute('aria-current','page'):a.removeAttribute('aria-current')});
 $('ptitle').textContent=PAGES[k][0];$('psub').textContent=PAGES[k][1];if(route.last!==k){window.scrollTo(0,0);route.last=k}
 $('symbar').classList.toggle('on',['overview','analyze','replay'].includes(k));$('symhint').style.display=k==='overview'?'none':'';
 if(k==='analyze'&&F.sym&&A.shown!==F.sym)runAnalysis();
 if(k==='history')loadHistory(); if(k==='replay'){liveCfgChip();schedule()} if(k==='market'){mCfgChip();schedM()} if(k==='gateway')gOpen(); if(k==='overview')loadOverview();}
window.addEventListener('hashchange',route);

/* ---------------- health ---------------- */
async function health(){try{const h=await api('/health');$('hdot').className='dot';$('hstat').textContent=h.public?'Public demo online':'Engine online';
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
  if(!cards.length){$('cards').innerHTML=`<div class="card empty">No stocks yet. Type a ticker such as <b>SPY</b> in the search box above.</div>`;return}
  for(const c of cards)STAT[c.symbol]=c.status;paintSymbar();
  $('cards').innerHTML=cards.map(c=>`<article class="card tk">
   <div class="tk-h"><div><div class="sym">${esc(c.symbol)}</div><div class="px">$${c.price.toFixed(2)} · ${esc(c.last)}</div></div>
   <span class="pill ${c.status}">${svg(STATUS[c.status][0])}${STATUS[c.status][1]}</span></div>
   ${spark(c.spark,c.status)}
   <div class="stats"><div><span>Drawdown now</span><b>${loss(c.drawdown_now)}</b></div><div><span>1-year return</span><b>${pct(c.ret_1y)}</b></div>
   <div><span>1-year volatility</span><b>${pct(c.vol_1y)}</b></div><div><span>1-day VaR 95%</span><b>${loss(c.var_95,2)}</b></div>
   <div><span>Worst fall ever</span><b>${loss(c.max_drawdown)}</b></div><div><span>Since</span><b>${esc(c.first.slice(0,4))}</b></div></div>
   <div class="tk-a"><a class="btn" href="#/analyze" data-sym="${esc(c.symbol)}">${svg('sliders')}Analyze</a>
   <a class="btn" href="#/replay" data-sym="${esc(c.symbol)}">${svg('pulse')}Replay</a>
   ${DOWNLOADS&&/^Yahoo/.test(c.source)?`<button class="btn up" type="button" data-up="${esc(c.symbol)}" title="Download the latest prices for ${esc(c.symbol)}" aria-label="Update ${esc(c.symbol)}">${svg('restart')}</button>`:''}</div></article>`).join('');
  $('cards').querySelectorAll('button[data-up]').forEach(b=>b.addEventListener('click',()=>updateStock(b.dataset.up,b)));
  $('cards').querySelectorAll('a[data-sym]').forEach(a=>a.addEventListener('click',()=>{setSym(a.dataset.sym)}));
 }catch(e){$('cards').innerHTML=`<div class="card empty">Could not load: ${esc(e.message)}</div>`}}

/* ---------------- form ---------------- */
const F=store.get('form',{sym:null,start:'',end:'',strat:'sma_cross',fast:50,slow:200,look:126,volOn:true,vol:15,ddOn:true,dd:15,cool:20,cap:100,fee:1,slip:2,luck:'1000'});
function setSym(s){F.sym=s;$('f-sym').value=s;$('l-sym').value=s;store.set('form',F);paintSymbar();paintPeriod()}
function readForm(){Object.assign(F,{sym:$('f-sym').value,start:$('f-start').value,end:$('f-end').value,fast:+$('f-fast').value,slow:+$('f-slow').value,
 look:+$('f-look').value,volOn:$('f-vol-on').checked,vol:+$('f-vol').value,ddOn:$('f-dd-on').checked,dd:+$('f-dd').value,cool:+$('f-cool').value,
 cap:+$('f-cap').value,fee:+$('f-fee').value,slip:+$('f-slip').value,luck:$('f-luck').value});store.set('form',F);paintForm()}
const PRESETS={off:{volOn:false,ddOn:false,cap:100},mod:{volOn:true,vol:20,ddOn:true,dd:20,cool:20,cap:100},
 con:{volOn:true,vol:10,ddOn:true,dd:10,cool:30,cap:80}};
function presetOf(){for(const [k,p] of Object.entries(PRESETS))if(Object.entries(p).every(([a,v])=>F[a]===v))return k;return null}
function paintPeriod(){const d=DATASETS[F.sym];let y=-1;
 if(!F.start&&!F.end)y=0;else if(d&&F.start&&!F.end)y=Math.round((Date.parse(d.end)-Date.parse(F.start))/3.15576e10);
 document.querySelectorAll('#f-period button').forEach(b=>b.classList.toggle('on',+b.dataset.y===y))}
function paintForm(){document.querySelectorAll('#f-strat button').forEach(b=>b.classList.toggle('on',b.dataset.v===F.strat));
 const pr=presetOf();document.querySelectorAll('#f-preset button').forEach(b=>b.classList.toggle('on',b.dataset.p===pr));paintPeriod();
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
/* Discrete choices (stock, strategy, preset, period) re-run at once; sliders
   and number boxes wait for the Run button. Saved results come back in ms. */
document.querySelectorAll('#f-strat button').forEach(b=>b.addEventListener('click',()=>{F.strat=b.dataset.v;readForm();runAnalysis()}));
document.querySelectorAll('#f-preset button').forEach(b=>b.addEventListener('click',()=>{Object.assign(F,PRESETS[b.dataset.p]);store.set('form',F);fillForm();runAnalysis()}));
document.querySelectorAll('#f-period button').forEach(b=>b.addEventListener('click',()=>{const y=+b.dataset.y,d=DATASETS[F.sym];
 if(!y||!d){F.start='';F.end=''}else{const e=new Date(d.end);e.setFullYear(e.getFullYear()-y);F.start=e.toISOString().slice(0,10);F.end=''}
 store.set('form',F);fillForm();runAnalysis()}));
$('f-sym').addEventListener('change',()=>switchSym($('f-sym').value));
const A={token:0,shown:null};
async function runAnalysis(){readForm();if(!F.sym)return;const my=++A.token,sym=F.sym;$('amsg').className='msg';
 $('amsg').textContent=`Running ${sym}…`;$('result').classList.add('busy');const t0=performance.now();
 try{const ids=[];let cached=true;
  for(const c of hasRules()?[cfg(false),cfg(true)]:[cfg(false)]){const r=await api('/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});ids.push(r.run_id);cached=cached&&r.cached}
  if(my!==A.token)return;                    // a newer switch happened meanwhile
  $('result').innerHTML=`<iframe title="Risk report for ${esc(sym)}" src="/report?runs=${ids.join(',')}&embed=1"></iframe>`;A.shown=sym;
  $('amsg').textContent=`${sym}: ${cached?'loaded from saved results':'computed'} in ${Math.round(performance.now()-t0)} ms.`;
  health();
 }catch(e){if(my===A.token){$('amsg').className='msg err';$('amsg').textContent=e.message}}finally{if(my===A.token)$('result').classList.remove('busy')}}
$('run').addEventListener('click',runAnalysis);

/* ---------------- replay ---------------- */
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
function plot(cv,series,opts){const dpr=window.devicePixelRatio||1,w=cv.clientWidth,h=+(cv.dataset.h||(cv.dataset.h=cv.getAttribute('height')));
 cv.width=w*dpr;cv.height=h*dpr;cv.style.height=h+'px';const g=cv.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,w,h);
 const n=L.days||1,pl=58,pr=8,pt=8,pb=22,pw=w-pl-pr,ph=h-pt-pb;
 let lo=opts.lo,hi=opts.hi;for(const s of series)for(const v of s){if(v<lo)lo=v;if(v>hi)hi=v}if(!isFinite(lo)||!isFinite(hi)){lo=0;hi=1}
 if(hi-lo<1e-9){if(opts.fill){lo=-0.01;hi=0}else{const d=Math.abs(hi)*0.001||1;lo-=d;hi+=d}}
 const raw=(hi-lo)/4,mag=Math.pow(10,Math.floor(Math.log10(raw))),step=[1,2,2.5,5,10].map(m=>m*mag).find(x=>x>=raw);
 lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
 const Y=v=>pt+ph*(1-(v-lo)/(hi-lo)),X=i=>pl+pw*i/Math.max(1,n-1);
 g.font='12px "IBM Plex Sans",system-ui,sans-serif';g.fillStyle=css('--ink3');g.strokeStyle=css('--grid');g.lineWidth=1;
 for(let v=lo;v<=hi+step*1e-6;v+=step){const y=Y(v);g.beginPath();g.moveTo(pl,y);g.lineTo(w-pr,y);g.stroke();g.textAlign='right';g.fillText(opts.fmt(v,step),pl-8,y+4)}
 g.textAlign='center';let last='';L.data.forEach((d,i)=>{const yr=d.d.slice(0,4);if(yr!==last){if(last)g.fillText(yr,X(d.t),h-6);last=yr}});
 const cols=[css('--base'),css('--accent')];
 series.forEach((s,k)=>{if(!s.length)return;g.beginPath();s.forEach((v,i)=>{const x=X(L.data[i].t),y=Y(v);i?g.lineTo(x,y):g.moveTo(x,y)});
  if(opts.fill){g.lineTo(X(L.data[s.length-1].t),Y(0));g.lineTo(X(L.data[0].t),Y(0));g.closePath();g.globalAlpha=.14;g.fillStyle=cols[k];g.fill();g.globalAlpha=1;
   g.beginPath();s.forEach((v,i)=>{const x=X(L.data[i].t),y=Y(v);i?g.lineTo(x,y):g.moveTo(x,y)})}
  g.strokeStyle=cols[k];g.lineWidth=2;g.lineJoin='round';g.stroke()});
 g.fillStyle=css('--warn');L.data.forEach(d=>{for(const [k] of d.ev)if(k==='DRAWDOWN_BREAKER'){const x=X(d.t);g.beginPath();g.moveTo(x-5,pt);g.lineTo(x+5,pt);g.lineTo(x,pt+8);g.fill()}})}
function draw(){plot($('c-eq'),[L.data.map(d=>d.eq[0]),L.data.map(d=>d.eq[1])],{lo:Infinity,hi:-Infinity,fmt:(v,st)=>st>=1000?'$'+Math.round(v/1000)+'k':'$'+Math.round(v).toLocaleString()});
 plot($('c-dd'),[L.data.map(d=>-d.dd[0]),L.data.map(d=>-d.dd[1])],{lo:0,hi:0,fill:true,fmt:(v,st)=>Math.abs(v)<1e-9?'0%':(v*100).toFixed(st<0.01?1:0).replace('-','−')+'%'})}
window.addEventListener('resize',schedule);
$('l-play').addEventListener('click',()=>{if(L.playing){stop();setPlaying(false)}else{if(L.ended)reset();start(L.data.length?L.t+1:0)}});
$('l-restart').addEventListener('click',()=>{reset();start(0)});
$('l-speed').addEventListener('change',()=>{if(L.playing)start(L.t+1)});
$('l-sym').addEventListener('change',()=>switchSym($('l-sym').value));

/* ---------------- live market ---------------- */
const MS=store.get('market',{sym:'BTC-USD',src:'auto'});
const M={es:null,on:false,bars:[],risk:null,alerts:0,push:false,sim:false,src:'',poll:0,lost:false,lastPx:null,trades:0,feed:null,
 lat:{calc:[],push:[],e2e:[]},ticks:[]};
const BRK={armed:['calm','check','Breaker armed'],warning:['elevated','alert','Near the breaker'],tripped:['halted','alert','Breaker would fire']};
function fmtPx(p){if(p==null)return'–';return'$'+(p>=1?p.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):p.toPrecision(4))}
function fms(v,d=0){if(v==null||!isFinite(v))return'–';if(v<1&&d===0)return'<1 ms';if(v>=10000)return(v/1000).toFixed(1)+' s';return v.toFixed(d)+' ms'}
const med=a=>{if(!a.length)return null;const s=[...a].sort((x,y)=>x-y);return s[s.length>>1]};
function keep(a,v,n=40){a.push(v);if(a.length>n)a.shift()}
function mCfgChip(){$('m-cfg').innerHTML=`Vol target ${F.vol}% · breaker ${F.dd}% · <a href="#/analyze">change</a>`}
function mAlert(level,icon,title,text){M.alerts++;$('m-count').textContent=M.alerts;
 const li=document.createElement('li');li.className=level;
 const tm=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
 li.innerHTML=`<span class="ai">${svg(icon)}</span><div><b>${esc(title)}</b><span>${esc(text)} · ${tm}</span></div>`;
 const feed=$('m-feed');if(feed.firstElementChild&&feed.firstElementChild.dataset.idle!==undefined)feed.innerHTML='';feed.prepend(li);while(feed.children.length>100)feed.lastChild.remove()}
function mLive(state,label){const el=$('m-live');el.className='live'+(state?' '+state:'');el.querySelector('span').textContent=label}
function mStop(){if(M.es){M.es.close();M.es=null}M.on=false;$('m-go').innerHTML=svg('play')+'Connect';mLive('','Offline');paintLiveChips()}
function mConnect(){mStop();const sym=($('m-sym').value.trim()||'BTC-USD').toUpperCase();$('m-sym').value=sym;
 MS.sym=sym;MS.src=$('m-src').value;store.set('market',MS);mCfgChip();
 Object.assign(M,{bars:[],risk:null,lost:false,lastPx:null,trades:0,feed:null,lat:{calc:[],push:[],e2e:[]},ticks:[]});
 $('m-symbig').textContent=sym;$('m-px').textContent='–';$('m-chg').textContent='';$('m-brk').style.display='none';schedM();
 const es=new EventSource(`/api/market?symbol=${encodeURIComponent(sym)}&source=${MS.src}&vol=${F.vol/100}&dd=${F.dd/100}`);
 M.es=es;M.on=true;M.sym=sym;$('m-go').innerHTML=svg('pause')+'Disconnect';mLive('wait','Connecting…');paintLiveChips();
 es.addEventListener('snap',e=>{const m=JSON.parse(e.data);Object.assign(M,{bars:m.bars,push:m.push,sim:m.simulated,src:m.source,poll:m.poll_seconds,lost:false});
  mLive('on','LIVE');
  $('m-srcline').innerHTML=`<span class="tag${m.simulated?' sim':m.push?'':' delay'}">${m.simulated?'SIMULATED':m.push?'REAL-TIME':'DELAYED'}</span> ${esc(m.source)} · `+
   (m.push?'every trade is pushed the moment it happens':`checked every ${m.poll_seconds} s`)+` · risk rules: vol target ${F.vol}%, breaker ${F.dd}%`;
  mAlert('info','radio',`Connected: ${sym}`,`Loaded ${m.bars.length} one-minute bars${m.push?'; streaming every trade':`; checking every ${m.poll_seconds} s`}`);
  mLat(m.lat);mRender(m.risk)});
 es.addEventListener('tick',e=>{const m=JSON.parse(e.data);for(const b of m.bars)mUpsert(b);keep(M.ticks,performance.now(),400);
  if($('m-live').classList.contains('stale'))mLive('on','LIVE');
  mLat(m.lat);mRender(m.risk);for(const [lv,t,x] of m.alerts)mAlert(lv,lv==='crit'?'alert':lv==='ok'?'check':'wave',t,x)});
 es.addEventListener('status',e=>{const s=JSON.parse(e.data);if(s.text==='heartbeat')return;
  if(s.text.startsWith('No new prices'))mLive('stale','NO NEW PRICES');
  mAlert(s.level,s.level==='ok'?'check':s.level==='info'?'info':'alert',s.level==='crit'?'Feed problem':s.level==='ok'?'Feed restored':'Feed notice',s.text);
  if(s.fatal)mStop()});
 es.onerror=()=>{if(M.es&&!M.lost){M.lost=true;mLive('wait','Reconnecting…');mAlert('crit','alert','Connection lost','Retrying automatically. Is the RiskLens server still running?')}}}
function mUpsert(b){const a=M.bars;let i=a.length-1;while(i>=0&&a[i][0]>b[0])i--;if(i>=0&&a[i][0]===b[0])a[i]=b;else a.splice(i+1,0,b);if(a.length>1440)a.shift()}
function mLat(l){if(!l)return;const now=Date.now();M.feed=l.feed_ms??null;M.trades=l.trades||0;keep(M.lat.calc,l.compute_ms);
 keep(M.lat.push,Math.max(0,now-l.sent_ms));if(l.last_trade_ms!=null)keep(M.lat.e2e,Math.max(0,now-l.last_trade_ms))}
function mRender(r){if(!r)return;M.risk=r;const px=$('m-px');px.textContent=fmtPx(r.price);
 if(M.lastPx!=null&&r.price!==M.lastPx){px.classList.remove('up','dn');void px.offsetWidth;px.classList.add(r.price>M.lastPx?'up':'dn')}M.lastPx=r.price;
 const c=$('m-chg');c.className='mchg '+(r.change>0?'pos':r.change<0?'neg':'');c.textContent=`${r.change>0?'+':''}${pct(r.change,2)} this session`;
 const b=BRK[r.breaker],p=$('m-brk');p.style.display='';p.className='pill '+b[0];p.innerHTML=svg(b[1])+b[2];
 const warm=n=>`Warming up: needs ${n} minutes of data`;
 $('m-x').textContent=r.exposure==null?'–':pct(r.exposure,0);$('m-x2').innerHTML=r.exposure==null?warm(10):`Target ${pct(r.vol_target,0)} ÷ current vol <b>${pct(r.vol,0)}</b>`;
 $('m-dd').textContent=loss(r.drawdown,2);$('m-dd2').innerHTML=`Breaker fires at <b>${pct(r.dd_limit,0)}</b> · high ${fmtPx(r.high)}`;
 $('m-vol').textContent=r.vol==null?'–':pct(r.vol,0);
 $('m-vol2').innerHTML=r.vol==null?warm(10):'Annualized · '+(r.vol>2*r.vol_target?'<b style="color:var(--warn)">over 2× your target</b>':r.vol>r.vol_target?'above your target':'within your target');
 $('m-var').textContent=r.var==null?'–':loss(r.var,3);$('m-var2').textContent=r.var==null?warm(20):'Worst 5% of minutes lose at least this';
 $('m-rng').textContent=`${fmtPx(r.low)} – ${fmtPx(r.high)}`;$('m-rng2').textContent=`${r.bars.toLocaleString()} one-minute bars`;
 schedM()}
function mTick(){if(!M.on||!M.src)return;const now=performance.now(),n=M.ticks.filter(t=>now-t<10000).length;
 $('m-act').textContent=M.push?`${(n/10).toFixed(1)}/s`:`${M.poll} s`;
 $('m-act2').textContent=M.push?`updates/s · ${M.trades.toLocaleString()} trades received`:'between checks of a polled feed';
 $('lt-feed').textContent=M.push?fms(M.feed==null?null:Math.max(0,M.feed)):M.sim?'n/a (simulated)':'≈ 1 min (delayed)';
 $('lt-calc').textContent=fms(med(M.lat.calc),2);$('lt-push').textContent=fms(med(M.lat.push));
 $('lt-e2e').textContent=M.push?fms(med(M.lat.e2e)):M.sim?'n/a':'≈ 1 min+';
 $('lt-note').textContent=M.push?'Medians of recent updates. Exchange timestamps are compared with your PC clock, so a few ms of clock error is normal. Bursts of trades are batched to at most 10 updates per second.'
  :M.sim?'Simulated prices: only the risk calculation and push times are real.':'Yahoo’s free data is about a minute behind and is checked every 20 s. Pick a crypto pair for Coinbase’s real-time feed.'}
setInterval(mTick,1000);
let rafM=0;function schedM(){if(!rafM)rafM=requestAnimationFrame(()=>{rafM=0;mdraw()})}
function niceRange(lo,hi,parts=4){if(!isFinite(lo)||!isFinite(hi)){lo=0;hi=1}if(hi===lo){const d=Math.abs(hi)*0.001||1;lo-=d;hi+=d}
 const raw=(hi-lo)/parts,mag=Math.pow(10,Math.floor(Math.log10(raw))),step=[1,2,2.5,5,10].map(m=>m*mag).find(x=>x>=raw);
 return{lo:Math.floor(lo/step)*step,hi:Math.ceil(hi/step)*step,step}}
function mplot(cv,series,o){const dpr=window.devicePixelRatio||1,w=cv.clientWidth,h=+(cv.dataset.h||(cv.dataset.h=cv.getAttribute('height')));if(!w)return;
 cv.width=w*dpr;cv.height=h*dpr;cv.style.height=h+'px';const g=cv.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,w,h);
 g.font='12px "IBM Plex Sans",system-ui,sans-serif';g.fillStyle=css('--ink3');
 const n=M.bars.length;if(!n){g.textAlign='center';g.fillText(M.on?'Waiting for prices…':'Not connected',w/2,h/2);return}
 let lo=Infinity,hi=-Infinity;for(const s of series)for(const v of s.v){if(v<lo)lo=v;if(v>hi)hi=v}
 for(const v of o.include||[]){if(v<lo)lo=v;if(v>hi)hi=v}
 const r=niceRange(lo,hi),pl=o.pl||64,pr=10,pt=8,pb=22,pw=w-pl-pr,ph=h-pt-pb;
 const Y=v=>pt+ph*(1-(v-r.lo)/(r.hi-r.lo)),X=i=>pl+pw*i/Math.max(1,n-1);
 g.strokeStyle=css('--grid');g.lineWidth=1;
 for(let v=r.lo;v<=r.hi+r.step*1e-6;v+=r.step){const y=Y(v);g.beginPath();g.moveTo(pl,y);g.lineTo(w-pr,y);g.stroke();g.textAlign='right';g.fillText(o.fmt(v,r.step),pl-8,y+4)}
 g.textAlign='center';const every=Math.max(1,Math.ceil(n/Math.max(2,Math.floor(pw/90))));
 for(let i=0;i<n;i+=every){const d=new Date(M.bars[i][0]);if(!isNaN(d))g.fillText(d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),X(i),h-6)}
 for(const s of series){g.beginPath();s.v.forEach((v,i)=>{const x=X(i),y=Y(v);i?g.lineTo(x,y):g.moveTo(x,y)});
  if(s.fill){g.lineTo(X(n-1),Y(0));g.lineTo(X(0),Y(0));g.closePath();g.globalAlpha=.14;g.fillStyle=s.color;g.fill();g.globalAlpha=1;
   g.beginPath();s.v.forEach((v,i)=>{const x=X(i),y=Y(v);i?g.lineTo(x,y):g.moveTo(x,y)})}
  g.setLineDash(s.dash||[]);g.strokeStyle=s.color;g.lineWidth=s.width||2;g.lineJoin='round';g.stroke();g.setLineDash([])}
 if(o.hline!=null){const y=Y(o.hline);g.setLineDash([6,4]);g.strokeStyle=css('--warn');g.lineWidth=1.5;g.beginPath();g.moveTo(pl,y);g.lineTo(w-pr,y);g.stroke();g.setLineDash([]);
  g.fillStyle=css('--warn');g.textAlign='left';g.fillText('breaker',pl+6,y-5)}
 const lx=X(n-1),ly=Y(series[0].v[n-1]);g.fillStyle=series[0].color;g.beginPath();g.arc(lx,ly,3.5,0,7);g.fill()}
function mdraw(){const closes=M.bars.map(b=>b[1]);let hi=-Infinity;const high=M.bars.map(b=>hi=Math.max(hi,b[2],b[1]));
 const dec=s=>s>=1?0:s>=0.1?1:s>=0.01?2:4;
 mplot($('c-mpx'),[{v:closes,color:css('--accent')},{v:high,color:css('--base'),dash:[5,4],width:1.5}],
  {pl:78,fmt:(v,s)=>'$'+v.toLocaleString(undefined,{minimumFractionDigits:dec(s),maximumFractionDigits:dec(s)})});
 const dd=M.bars.map((b,i)=>-(1-b[1]/high[i]));const lim=M.risk?-M.risk.dd_limit:null;
 mplot($('c-mdd'),[{v:dd,color:css('--bad'),fill:true}],{pl:78,include:[0,...(lim!=null&&Math.min(...dd,0)<lim*0.5?[lim]:[])],
  hline:lim!=null&&Math.min(...dd,0)<lim*0.5?lim:null,fmt:(v,s)=>Math.abs(v)<1e-12?'0%':(v*100).toFixed(s>=0.01?0:s>=0.001?1:2).replace('-','−')+'%'})}
window.addEventListener('resize',schedM);
$('m-sym').value=MS.sym;$('m-src').value=MS.src;$('m-symbig').textContent=MS.sym;
$('m-go').addEventListener('click',()=>{M.on?mStop():mConnect()});
$('m-sym').addEventListener('keydown',e=>{if(e.key==='Enter')mConnect()});
function liveSyms(){const st=Object.keys(DATASETS).filter(x=>x!=='SYNTH');
 return['BTC-USD','ETH-USD','SOL-USD',...(st.length?st:['SPY','AAPL'])].filter((x,i,a)=>a.indexOf(x)===i).slice(0,12)}
function paintLiveChips(){const cur=M.on?M.sym:'';const chip=x=>`<button class="qchip${x===cur?' on':''}" type="button" data-s="${esc(x)}">${esc(x.endsWith('-USD')?x.slice(0,-4):x)}</button>`;
 const html=liveSyms().map(chip).join('');$('m-quick').innerHTML=html;$('g-quick').innerHTML=html;
 for(const id of ['m-quick','g-quick'])$(id).querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{$('m-sym').value=b.dataset.s;mConnect();
  paintLiveChips();if(id==='g-quick')setTimeout(gFeed,600)}))}
$('m-src').addEventListener('change',()=>{MS.src=$('m-src').value;store.set('market',MS);if(M.on)mConnect()});

/* ---------------- risk gateway ---------------- */
const SID=(()=>{let s=store.get('sid',null);if(!s||!/^[A-Za-z0-9_-]{8,64}$/.test(s)){const a=new Uint8Array(12);crypto.getRandomValues(a);
 s='s'+[...a].map(x=>x.toString(16).padStart(2,'0')).join('');store.set('sid',s)}return s})();
const G={side:'buy',st:null,lastId:0,kill:null,lim:store.get('glim',{mo:25000,mp:50000,rate:30})};
async function gapi(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-RiskLens-Session':SID},
 body:body===undefined?undefined:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
const gsym=()=>(M.on&&M.sym)?M.sym:(MS.sym||'BTC-USD');
function gQuote(){const q=G.st&&G.st.quotes[gsym()];return q||null}
function gLimitsBody(){return{max_order_value:G.lim.mo,max_position_value:G.lim.mp,max_orders_per_minute:G.lim.rate,
 max_exposure:F.cap/100,vol_target:F.volOn?F.vol/100:null,kill_drawdown:F.ddOn?F.dd/100:null}}
async function gOpen(){$('g-mo').value=G.lim.mo;$('g-mp').value=G.lim.mp;$('g-rate').value=G.lim.rate;
 $('g-session').textContent='Paper account '+SID.slice(1,7);
 try{gRender(await gapi('/api/gateway/limits',gLimitsBody()))}catch(e){$('g-verdict').textContent=e.message}}
function gFeed(){const sym=gsym(),q=gQuote();$('g-sym').textContent=sym;
 const f=$('g-feed');if(M.on&&M.risk){f.innerHTML=`<span class="dot"></span>Live: <b>${esc(sym)}</b> ${fmtPx(M.risk.price)}${M.sim?' (simulated)':''}`;}
 else{f.textContent=q&&q.fresh?`Last price ${esc(sym)} ${fmtPx(q.price)}`:'No live price yet: pick a symbol (orders without one are rejected)'}
 const px=M.on&&M.risk?M.risk.price:q?q.price:null,qty=parseFloat($('g-qty').value);
 $('g-est').textContent=px&&qty>0?`≈ ${money(qty*px)} at ${fmtPx(px)}`:''}
function gRender(s){G.st=s;
 $('g-eq').textContent=money(s.equity);const p=$('g-pnl');p.className='pnl '+(s.pnl>0?'pos':s.pnl<0?'neg':'');
 p.textContent=`${s.pnl>=0?'+':'−'}${money(Math.abs(s.pnl))} since start`;
 $('g-cash').textContent=money(s.cash);$('g-fees').textContent='$'+s.fees.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 $('g-hold').innerHTML=s.positions.length?s.positions.map(x=>`${esc(x.qty)} ${esc(x.symbol)} · ${money(x.value)}${x.stale?' <span class="muted">(stale)</span>':''}`).join('<br>'):'none';
 const q=s.quotes[gsym()],allowed=q?q.allowed_exposure:s.limits.max_exposure;
 $('g-exl').textContent=`${pct(s.exposure,0)} of equity · allowed ${pct(allowed,0)}`;
 $('g-exb').style.width=Math.min(100,s.exposure*100)+'%';$('g-exb').classList.toggle('hot',s.exposure>allowed+1e-9);$('g-exm').style.left=Math.min(100,allowed*100)+'%';
 const kd=s.limits.kill_drawdown,ddt=s.drawdown<0.0005?'0.0%':loss(s.drawdown);$('g-ddl').textContent=kd?`${ddt} · kill switch at ${pct(kd,0)}`:`${ddt} · auto kill off`;
 const sc=kd?Math.max(kd*1.25,s.drawdown):Math.max(0.2,s.drawdown);
 $('g-ddb').style.width=Math.min(100,s.drawdown/sc*100)+'%';$('g-ddb').classList.toggle('hot',kd&&s.drawdown>=0.7*kd);$('g-ddm').style.left=kd?(kd/sc*100)+'%':'-10px';
 const r=$('g-recon');r.className='recon'+(s.reconciles?'':' bad');
 r.innerHTML=svg(s.reconciles?'check':'alert')+(s.reconciles?'Books reconcile exactly: cash = start − bought + sold − fees':'Books do not reconcile!');
 const L=s.limits;$('g-rules').innerHTML=`From your risk rules: exposure cap ${pct(L.max_exposure,0)}, vol target ${L.vol_target?pct(L.vol_target,0):'off'}, `+
  `auto kill switch at ${L.kill_drawdown?pct(L.kill_drawdown,0)+' below peak':'off'} · <a href="#/analyze">change</a>`;
 const st=s.stats;$('g-stats').textContent=st.checked?`${st.checked} checked · ${st.approved} approved · ${st.rejected} rejected · median check ${st.p50_us} µs`:'';
 // kill switch
 const k=s.kill;$('g-killbox').classList.toggle('on',!!k);
 $('g-kill').innerHTML=svg('power')+(k?'Kill switch on':'Kill switch');$('g-kill').disabled=!!k;
 if(k){$('g-killt').textContent=k.by==='auto'?'Kill switch tripped automatically':'Kill switch engaged';
  $('g-killd').textContent=`${k.time} · ${k.reason}. Positions were closed and new buys are blocked until you re-arm.`}
 if(k&&!G.kill)mAlert('crit','power',k.by==='auto'?'Kill switch tripped':'Kill switch engaged',`${k.reason}. Positions closed, buys blocked.`);
 G.kill=k;
 // log
 $('g-log').innerHTML=s.log.length?s.log.map(d=>`<tr><td>${esc(d.time)}</td><td><b>${esc(d.side||'')}</b> ${esc(d.qty||'')} ${esc(d.symbol||'')}</td>`+
  `<td class="num">${d.price!=null?fmtPx(d.price):'–'}</td><td class="num">${d.value!=null?money(d.value):'–'}</td>`+
  `<td><span class="res ${d.flatten?'fl':d.approved?'ok':'no'}">${d.flatten?'Flattened':d.approved?'Approved':'Rejected'}</span></td>`+
  `<td class="why">${esc(d.reason)}</td><td class="num">${d.check_us!=null?Math.round(d.check_us)+' µs':''}</td></tr>`).join('')
  :'<tr><td colspan="7" class="empty">No orders yet.</td></tr>';
 gFeed()}
function gShow(d){const ok=d.approved;$('g-vtime').textContent=d.check_us!=null?`all checks in ${d.check_us} µs`:'';
 $('g-verdict').innerHTML=`<span class="verd ${ok?'ok':'no'}">${svg(ok?'check':'alert')}${ok?'Approved and filled':'Rejected'}</span> `+
  `<span class="note">${esc(d.side||'')} ${esc(d.qty||'')} ${esc(d.symbol||'')}${d.price?' at '+fmtPx(d.price):''}${ok?'':' · '+esc(d.reason)}</span>`;
 const names=['Valid order','Live price','Kill switch','Order size','Order rate',d.side==='sell'?'No short selling':'Enough cash','Position limit','Exposure limit'];
 const by={};for(const c of d.checks)by[c.name]=c;
 $('g-checks').innerHTML=names.map(n=>{const c=by[n];if(!c)return`<li><span class="muted">–</span><b class="muted">${n}</b><span class="muted">not reached</span></li>`;
  return`<li class="${c.ok?'ok':'no'}">${svg(c.ok?'check':'alert')}<b>${n}</b><span>${esc(c.detail)}</span></li>`}).join('');
 if(!ok)mAlert('warn','shield','Order rejected by the gateway',`${d.side||''} ${d.qty||''} ${d.symbol||''}: ${d.reason}`)}
async function gSend(){const b=$('g-send');b.disabled=true;
 try{const r=await gapi('/api/gateway/order',{symbol:gsym(),side:G.side,qty:$('g-qty').value.trim()});gShow(r.decision);gRender(r.state)}
 catch(e){$('g-verdict').textContent=e.message}finally{b.disabled=false}}
document.querySelectorAll('#g-side button').forEach(b=>b.addEventListener('click',()=>{G.side=b.dataset.v;
 document.querySelectorAll('#g-side button').forEach(x=>x.classList.toggle('on',x===b))}));
$('g-send').addEventListener('click',gSend);
$('g-qty').addEventListener('keydown',e=>{if(e.key==='Enter')gSend()});
$('g-qty').addEventListener('input',gFeed);
document.querySelectorAll('#g-quick button').forEach(b=>b.addEventListener('click',()=>{const px=M.on&&M.risk?M.risk.price:(gQuote()||{}).price;
 if(!px||!G.st)return;const q=G.st.equity*(+b.dataset.p)/px;$('g-qty').value=(q>=1?q.toFixed(4):q.toPrecision(4)).replace(/\.?0+$/,'');gFeed()}));
$('g-kill').addEventListener('click',async()=>{try{gRender(await gapi('/api/gateway/kill',{flatten:true}))}catch(e){$('g-verdict').textContent=e.message}});
$('g-rearm').addEventListener('click',async()=>{try{gRender(await gapi('/api/gateway/rearm',{}));mAlert('ok','check','Kill switch re-armed','Buys allowed again; drawdown measured from here.')}catch(e){}});
$('g-reset').addEventListener('click',async()=>{try{gRender(await gapi('/api/gateway/reset',{}));$('g-checks').innerHTML='';$('g-verdict').textContent='Account reset to $100,000.'}catch(e){}});
for(const [id,k] of [['g-mo','mo'],['g-mp','mp'],['g-rate','rate']])$(id).addEventListener('change',async()=>{const v=+$(id).value;if(!(v>0))return;
 G.lim[k]=k==='rate'?Math.round(v):v;store.set('glim',G.lim);try{gRender(await gapi('/api/gateway/limits',gLimitsBody()))}catch(e){$('g-verdict').textContent=e.message}});
setInterval(async()=>{if(!$('p-gateway').classList.contains('on')&&!M.on)return;try{gRender(await gapi('/api/gateway'))}catch(e){}},1000);

/* ---------------- history ---------------- */
async function loadHistory(){try{const rows=await api('/api/history');
 $('hist').innerHTML=rows.length?rows.map(r=>{const s=r.strategy,p=Object.values(s.params).join('/');const k=r.risk;
  const rules=[k.vol_target?`vol ${Math.round(k.vol_target*100)}%`:null,k.max_drawdown?`breaker ${Math.round(k.max_drawdown*100)}%`:null,k.max_exposure<1?`cap ${Math.round(k.max_exposure*100)}%`:null].filter(Boolean).join(', ')||'none';
  return `<tr><td>${esc(r.saved)}</td><td><b>${esc(r.symbol)}</b></td><td>${esc({sma_cross:'Trend',momentum:'Momentum',buy_and_hold:'Buy & hold'}[s.name]||s.name)}${p?' '+esc(p):''}</td><td>${esc(rules)}</td>
  <td>${esc(r.start.slice(0,7))} to ${esc(r.end.slice(0,7))}</td><td class="num">${pct(r.cagr)}</td><td class="num">${loss(r.max_drawdown)}</td><td class="num">${r.sharpe.toFixed(2)}</td>
  <td><a class="btn" style="padding:5px 11px;font-size:14px" href="/report?runs=${r.run_id}" target="_blank" rel="noopener" title="Open this report full screen in a new tab">${svg('external')}Open</a></td></tr>`}).join('')
  :'<tr><td colspan="9" class="empty">No analyses yet. Run one from Analyze.</td></tr>'}
 catch(e){$('hist').innerHTML=`<tr><td colspan="9" class="empty">${esc(e.message)}</td></tr>`}}

/* ---------------- stock bar ---------------- */
const STAT={};let PUBLIC=false,DOWNLOADS=true;
const curPage=()=>{const p=(location.hash.match(/#\/(\w+)/)||[])[1];return PAGES[p]?p:'overview'};
function paintSymbar(){const syms=Object.keys(DATASETS),el=$('symchips');
 el.innerHTML=syms.length?syms.map(x=>`<button type="button" class="symchip${x===F.sym?' on':''}" data-s="${esc(x)}" title="${esc(DATASETS[x].start)} to ${esc(DATASETS[x].end)}, ${(+DATASETS[x].rows).toLocaleString()} days">`+
  `${STAT[x]?`<i class="sd ${STAT[x]}"></i>`:''}${esc(x)}</button>`).join(''):'<span class="symmsg">No stocks yet</span>';
 el.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>switchSym(b.dataset.s)))}
function switchSym(x){if(!x||!DATASETS[x])return;const pg=curPage();setSym(x);
 if(pg==='overview'){location.hash='#/analyze';return}       // route() runs the analysis
 if(pg==='analyze')runAnalysis();
 if(pg==='replay'){const was=L.playing;reset();setPlaying(false);$('l-date').textContent='Ready';if(was)start(0)}}
document.addEventListener('keydown',e=>{const pg=curPage();if(pg!=='analyze'&&pg!=='replay')return;
 if(e.target.closest('input,select,textarea,[contenteditable]')||e.altKey||e.ctrlKey||e.metaKey)return;
 if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;const syms=Object.keys(DATASETS);if(syms.length<2)return;
 const i=syms.indexOf(F.sym);e.preventDefault();switchSym(syms[(i+(e.key==='ArrowRight'?1:-1)+syms.length)%syms.length])});
function symMsg(t,cls=''){const m=$('symmsg');m.className='symmsg '+cls;m.textContent=t}
/* Type any ticker: a stock you already have opens at once; a new one is
   downloaded the first time (frozen and fingerprinted), then analyzed. */
function goTo(x){setSym(x);const pg=curPage();if(pg==='analyze')runAnalysis();else if(pg==='replay')switchSym(x);else location.hash='#/analyze'}
const dlHelp=m=>/yfinance/.test(m)?'Downloading needs the yfinance package. Close RiskLens and start it again with Start RiskLens, which installs it (or run: pip install yfinance).':m;
async function analyzeTicker(){const x=$('add-sym').value.trim().toUpperCase();if(!x){$('add-sym').focus();return}
 if(DATASETS[x]){$('add-sym').value='';symMsg('');goTo(x);return}
 if(!DOWNLOADS){symMsg('This public demo can’t download new stocks. Pick one below, or run RiskLens on your own computer to analyze any ticker.','err');return}
 const b=$('add-go');b.disabled=true;symMsg(`Downloading ${x} price history (first time only, a few seconds)…`);
 try{const e=await api('/api/datasets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:x,start:$('add-from').value})});
  await loadDatasets();$('add-sym').value='';
  symMsg(`${e.symbol}: ${(+e.rows).toLocaleString()} trading days, ${e.start} to ${e.end}`,'ok');setTimeout(()=>symMsg(''),9000);goTo(e.symbol);
  if(curPage()!=='overview')loadOverview()}                 // refresh risk dots in the background
 catch(err){symMsg(dlHelp(err.message),'err')}finally{b.disabled=false}}
async function updateStock(x,btn){if(!DATASETS[x])return;btn&&(btn.disabled=true);symMsg(`Updating ${x} to the latest prices…`);
 try{const e=await api('/api/datasets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:x,start:DATASETS[x].start})});
  await loadDatasets();A.shown=null;symMsg(`${x} updated: prices through ${e.end}`,'ok');setTimeout(()=>symMsg(''),8000);if(curPage()==='overview')loadOverview()}
 catch(err){symMsg(dlHelp(err.message),'err')}finally{btn&&(btn.disabled=false)}}
$('add-go').addEventListener('click',analyzeTicker);
$('add-sym').addEventListener('keydown',e=>{if(e.key==='Escape'){$('add-sym').value='';symMsg('');$('add-sym').blur()}});
document.addEventListener('keydown',e=>{if(e.key!=='/'||e.target.closest('input,select,textarea,[contenteditable]'))return;
 if(!$('symbar').classList.contains('on'))return;e.preventDefault();$('add-sym').focus()});
async function loadDatasets(){try{DATASETS=await api('/datasets')}catch(e){DATASETS={}}
 const syms=Object.keys(DATASETS);const opts=syms.map(x=>`<option>${esc(x)}</option>`).join('');$('f-sym').innerHTML=opts;$('l-sym').innerHTML=opts;
 if(!F.sym||!syms.includes(F.sym))F.sym=syms.includes('SPY')?'SPY':syms[0]||null;
 if(F.sym){$('f-sym').value=F.sym;$('l-sym').value=F.sym}paintSymbar();paintLiveChips();return syms}

/* ---------------- boot ---------------- */
(async()=>{await loadDatasets();if(F.sym)setSym(F.sym);fillForm();liveCfgChip();
 try{const h=await api('/health');PUBLIC=h.public;DOWNLOADS=h.downloads!==false;
  if(!DOWNLOADS)$('add-sym').placeholder='Find a stock on this demo, e.g. SYNTH'}catch(e){}
 health();route();draw();if(curPage()!=='overview')loadOverview()})();
</script></body></html>"""


def page(version: str) -> str:
    return PAGE.replace("__ICONS__", _icons_json()).replace("__VERSION__", version)
