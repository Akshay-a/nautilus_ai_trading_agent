#!/usr/bin/env python3
"""
Log Dashboard Generator
Parses deepseek_trader_*.json or trader_*.log and generates a self-contained HTML dashboard.
Usage: python log_dashboard.py [logfile] [hours]
"""
import re, sys, glob, json, os
from datetime import datetime, timedelta

def find_latest_log():
    json_logs = sorted(glob.glob("logs/deepseek_trader_*.json"), key=os.path.getmtime)
    if json_logs:
        return json_logs[-1]
    text_logs = sorted(glob.glob("logs/trader_*.log"), key=os.path.getmtime)
    return text_logs[-1] if text_logs else None

ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def strip_ansi(s):
    return ANSI_RE.sub('', s)

def parse_ts(line):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)", line)
    return m.group(1) if m else None


def classify_event(msg: str, level: str):
    patterns = [
        ("BAR_CLOSE", r"^📌 Bar-close @"),
        ("RISK_CONTEXT", r"^💼 Bybit Risk Context:"),
        ("POSITION_STATE", r"^Current Position:"),
        ("POSITION_MISMATCH", r"^⚠️ Nautilus/Bybit position mismatch:"),
        ("POSITION_EXCHANGE_FALLBACK", r"^⚠️ Nautilus cache has no open position but Bybit reports"),
        ("LLM_CALL", r"^Calling DeepSeek AI"),
        ("LLM_CONTEXT", r"^🤖 LLM Context:"),
        ("LLM_PROMPT_MICRO", r"^🤖 Prompt microstructure section included:"),
        ("LLM_RAW_RESPONSE", r"^🤖 DeepSeek Raw Response:"),
        ("LLM_RESPONSE_JSON", r"^🤖 LLM Response JSON:"),
        ("LLM_SIGNAL", r"^🤖 Signal:"),
        ("ACTION_HOLD", r"^📊 Signal: HOLD - No action taken$"),
        ("ACTION_CONFIDENCE_SKIP", r"^⚠️ Signal confidence .* skipping trade$"),
        ("GIVEBACK_EXIT", r"^🛡️ GIVE-BACK PROTECTION:"),
        ("PARTIAL_CLOSE", r"^✂️ Partial close from LLM:"),
        ("ORDER_SUBMIT", r"^📤 Submitted (BUY|SELL) market order:"),
        ("BRACKET_SUBMIT", r"^✅ Submitted bracket order:"),
        ("ORDER_FILLED_EVENT", r"^<--\[EVT\] OrderFilled\("),
        ("POSITION_OPENED_EVENT", r"^<--\[EVT\] PositionOpened\("),
        ("POSITION_CHANGED_EVENT", r"^<--\[EVT\] PositionChanged\("),
        ("POSITION_CLOSED_EVENT", r"^<--\[EVT\] PositionClosed\("),
        ("ORDER_REJECTED_EVENT", r"^<--\[EVT\] OrderRejected\("),
        ("ORDER_REJECTED", r"^❌ Order rejected:"),
        ("ANALYSIS_FAILURE", r"^❌ Analysis attempt \d+ failed:"),
        ("PARSER_FAILURE", r"^❌ JSON parse failed"),
        ("BYBIT_CONTEXT_PARTIAL_FAILURE", r"^⚠️ Bybit account context partial failure:"),
        ("ORDERBOOK_SNAPSHOT", r"^📊 OB #"),
        ("TRAILING_STOP_UPDATE", r"^(⬆️ Trailing Stop Update|📍 Initial Trailing Stop|✅ New trailing SL order submitted)"),
    ]
    for event_type, pattern in patterns:
        if re.search(pattern, msg):
            return event_type
    if level in ("ERROR", "WARN"):
        return f"{level}_GENERIC"
    return None


def parse_line(line: str):
    raw = line.rstrip("\n")
    stripped = strip_ansi(raw)
    if stripped.startswith("{") and '"timestamp"' in stripped and '"message"' in stripped:
        try:
            payload = json.loads(stripped)
            ts = payload.get("timestamp")
            if ts:
                m_ts = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?$", ts)
                if m_ts:
                    frac = m_ts.group(2) or ""
                    frac = (frac + "000000")[:6]
                    ts = m_ts.group(1) + (f".{frac}" if frac and int(frac) else "")
            return {
                "ts": ts,
                "level": str(payload.get("level", "")),
                "component": str(payload.get("component", "")),
                "msg": str(payload.get("message", "")),
            }
        except Exception:
            pass

    ts = parse_ts(stripped)
    level = ""
    m_level = re.search(r"\[(INFO|WARN|ERROR|DEBUG)\]", stripped)
    if m_level:
        level = m_level.group(1)
    return {
        "ts": ts,
        "level": level,
        "component": "",
        "msg": stripped,
    }


def parse_log(path, hours=3):
    signals, ob_data, portfolio, positions, errors, fills, trailing = [], [], [], [], [], [], []
    llm_ctx, events = [], []
    
    from datetime import timezone
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    
    with open(path, "r") as f:
        for line in f:
            parsed = parse_line(line)
            ts_str = parsed.get("ts")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
            except:
                continue
            if ts < cutoff:
                continue
            ts_js = ts_str  # ISO string for JS
            msg = parsed.get("msg", "")
            level = parsed.get("level", "")
            component = parsed.get("component", "")

            event_type = classify_event(msg, level)
            if event_type:
                events.append({
                    "ts": ts_js,
                    "type": event_type,
                    "level": level,
                    "component": component,
                    "msg": msg[:800],
                })
            
            # LLM Signals
            m = re.search(r"🤖 Signal: (\w+) \| Confidence: (\w+) \| API time: ([\d.]+)s \| Reason: (.+?)$", msg)
            if m:
                signals.append({"ts": ts_js, "signal": m.group(1), "confidence": m.group(2),
                               "api_time": float(m.group(3)), "reason": m.group(4).strip()})
                continue

            # Orderbook snapshots
            m = re.search(r"📊 OB #(\d+): bid=([\d.]+) ask=([\d.]+) spr=([\d.]+)bps tob=([+\-\d.]+) ofi=([+\-\d.]+) qp=([+\-\d.]+) tf=([+\-\d.]+) sw_b=(\d+) sw_s=(\d+) regime=(\w+)", msg)
            if m:
                ob_data.append({"ts": ts_js, "seq": int(m.group(1)), "bid": float(m.group(2)),
                               "ask": float(m.group(3)), "spread_bps": float(m.group(4)),
                               "tob": float(m.group(5)), "ofi": float(m.group(6)),
                               "qp": float(m.group(7)), "tf": float(m.group(8)),
                               "sw_b": int(m.group(9)), "sw_s": int(m.group(10)),
                               "regime": m.group(11)})
                continue

            # Portfolio / Risk context
            m = re.search(r"💼 Bybit Risk Context: available=([\d.]+) equity=([\d.]+) position=(\w+)\s*([\d.]*)\s*[A-Z]{2,8} open_orders=(\d+) last5_pnl=([+\-\d.]+)", msg)
            if m:
                portfolio.append({"ts": ts_js, "available": float(m.group(1)),
                                 "equity": float(m.group(2)), "side": m.group(3),
                                 "qty": float(m.group(4)) if m.group(4) else 0,
                                 "open_orders": int(m.group(5)), "last5_pnl": float(m.group(6))})
                continue

            # Position context
            m = re.search(r"Current Position: (\w+) ([\d.]+) [A-Z]{2,8} @ \$([\d.]+) uPnL=([+\-\d.]+) peak=([+\-\d.]+) giveback=(\d+)% bars_held=(\d+) health=(\w+)", msg)
            if m:
                positions.append({"ts": ts_js, "side": m.group(1), "qty": float(m.group(2)),
                                 "entry": float(m.group(3)), "upnl": float(m.group(4)),
                                 "peak": float(m.group(5)), "giveback": int(m.group(6)),
                                 "bars_held": int(m.group(7)), "health": m.group(8)})
                continue

            # Order fills
            m = re.search(r"OrderFilled.*order_side=(\w+).*last_qty=([\d.]+).*last_px=([\d_,.]+)\s*USDT.*commission=([\d.]+)", msg)
            if m:
                px = m.group(3).replace("_", "").replace(",", "")
                fills.append({"ts": ts_js, "side": m.group(1), "qty": float(m.group(2)),
                             "px": float(px), "commission": float(m.group(4))})
                continue

            # Trailing stops
            m = re.search(r"Trailing Stop Update.*New SL: \$([\d,.]+)", msg)
            if m:
                trailing.append({"ts": ts_js, "sl": float(m.group(1).replace(",", ""))})
                continue

            # Errors and warnings
            if level in ("ERROR", "WARN") or "[ERROR]" in msg or "[WARN]" in msg:
                err_level = level if level in ("ERROR", "WARN") else ("ERROR" if "[ERROR]" in msg else "WARN")
                cleaned_msg = msg[:400]
                errors.append({"ts": ts_js, "level": err_level, "msg": cleaned_msg})
                continue

            # LLM context
            m = re.search(r"🤖 LLM Context: px=([\d.]+) pos=(\w+) qty=([+\-\d.]+|-) upnl=([+\-\d.]+|-) trend=(\w+) rsi=([\d.]+) rvol=([\d.]+) vol_regime=(\w+) tfi=([+\-\d.]+)", msg)
            if m:
                llm_ctx.append({"ts": ts_js, "px": float(m.group(1)), "pos": m.group(2),
                               "trend": m.group(5), "rsi": float(m.group(6)),
                               "rvol": float(m.group(7)), "vol_regime": m.group(8),
                               "tfi": float(m.group(9))})
                continue

    return {"signals": signals, "ob": ob_data, "portfolio": portfolio, "positions": positions,
            "errors": errors, "fills": fills, "trailing": trailing, "llm_ctx": llm_ctx, "events": events}

def generate_html(data, log_path, hours):
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Log Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',sans-serif;background:#0a0e17;color:#e0e6ed;min-height:100vh}}
.header{{background:linear-gradient(135deg,#0d1321 0%,#1a1f35 100%);padding:20px 32px;border-bottom:1px solid #1e2a42;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:22px;font-weight:700;background:linear-gradient(90deg,#4fc3f7,#7c4dff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.header .meta{{font-size:12px;color:#7b8ba5}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:20px 32px}}
.grid.full{{grid-template-columns:1fr}}
.card{{background:linear-gradient(145deg,#111827 0%,#0f1729 100%);border:1px solid #1e2a42;border-radius:12px;padding:16px;overflow:hidden}}
.card h2{{font-size:14px;font-weight:600;color:#7c8db5;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.card h2 span{{font-size:16px}}
.kpi-row{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;padding:0 32px 4px}}
.kpi{{background:linear-gradient(145deg,#111827,#0f1729);border:1px solid #1e2a42;border-radius:10px;padding:14px 16px;text-align:center}}
.kpi .label{{font-size:11px;color:#5b6b85;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}}
.kpi .value{{font-size:22px;font-weight:700}}
.kpi .value.green{{color:#22c55e}}.kpi .value.red{{color:#ef4444}}.kpi .value.blue{{color:#3b82f6}}.kpi .value.amber{{color:#f59e0b}}.kpi .value.purple{{color:#a855f7}}
canvas{{max-height:260px}}
.signal-table{{width:100%;border-collapse:collapse;font-size:12px}}
.signal-table th{{text-align:left;padding:6px 8px;color:#5b6b85;border-bottom:1px solid #1e2a42;font-weight:500}}
.signal-table td{{padding:6px 8px;border-bottom:1px solid #0d1321}}
.signal-table tr:hover{{background:#1a1f35}}
.badge{{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.badge.BUY{{background:#22c55e22;color:#22c55e}}.badge.SELL{{background:#ef444422;color:#ef4444}}.badge.HOLD{{background:#f59e0b22;color:#f59e0b}}
.badge.HIGH{{background:#22c55e22;color:#22c55e}}.badge.MEDIUM{{background:#3b82f622;color:#3b82f6}}.badge.LOW{{background:#f59e0b22;color:#f59e0b}}
.err-list{{max-height:250px;overflow-y:auto;font-size:11px}}
.err-item{{padding:6px 8px;border-left:3px solid;margin-bottom:4px;background:#0d132180}}
.err-item.ERROR{{border-color:#ef4444}}.err-item.WARN{{border-color:#f59e0b}}
.err-item .ts{{color:#5b6b85;font-size:10px}}
.fill-table{{width:100%;border-collapse:collapse;font-size:12px}}
.fill-table th{{text-align:left;padding:5px 8px;color:#5b6b85;border-bottom:1px solid #1e2a42}}
.fill-table td{{padding:5px 8px;border-bottom:1px solid #0d1321}}
.trace-controls{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}}
.trace-search{{background:#0d1321;border:1px solid #26344f;border-radius:6px;color:#dbe5f6;padding:6px 10px;min-width:280px}}
.trace-filter{{display:inline-flex;align-items:center;gap:6px;background:#0d1321;border:1px solid #26344f;border-radius:14px;padding:4px 8px;font-size:11px}}
.trace-table{{width:100%;border-collapse:collapse;font-size:11px}}
.trace-table th{{text-align:left;padding:6px 8px;color:#5b6b85;border-bottom:1px solid #1e2a42;font-weight:500}}
.trace-table td{{padding:6px 8px;border-bottom:1px solid #0d1321;vertical-align:top}}
.trace-wrap{{max-height:360px;overflow:auto}}
</style></head><body>
<div class="header">
  <h1>🚀 DeepSeek AI Trading Dashboard</h1>
  <div class="meta">Log: {os.path.basename(log_path)} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Last {hours} hours</div>
</div>

<div class="kpi-row" id="kpis"></div>
<div class="grid full"><div class="card"><h2><span>📈</span> Price & Trade Signals</h2><canvas id="priceChart"></canvas></div></div>
<div class="grid">
  <div class="card"><h2><span>📊</span> Orderbook Microstructure (OFI / TFI)</h2><canvas id="obChart"></canvas></div>
  <div class="card"><h2><span>💰</span> Portfolio Equity</h2><canvas id="eqChart"></canvas></div>
</div>
<div class="grid">
  <div class="card"><h2><span>🧠</span> Indicators (RSI / RVOL)</h2><canvas id="indChart"></canvas></div>
  <div class="card"><h2><span>⏱️</span> LLM API Latency</h2><canvas id="latChart"></canvas></div>
</div>
<div class="grid">
  <div class="card"><h2><span>🤖</span> Signal Log</h2><div style="max-height:280px;overflow-y:auto"><table class="signal-table" id="sigTable"><thead><tr><th>Time</th><th>Signal</th><th>Conf</th><th>API(s)</th><th>Reason</th></tr></thead><tbody></tbody></table></div></div>
  <div class="card"><h2><span>⚠️</span> Errors & Warnings ({len(data['errors'])})</h2><div class="err-list" id="errList"></div></div>
</div>
<div class="grid">
  <div class="card"><h2><span>💱</span> Order Fills</h2><div style="max-height:260px;overflow-y:auto"><table class="fill-table" id="fillTable"><thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>Commission</th></tr></thead><tbody></tbody></table></div></div>
  <div class="card"><h2><span>📉</span> Sweep Imbalance</h2><canvas id="swChart"></canvas></div>
</div>
<div class="grid full">
  <div class="card">
    <h2><span>🧭</span> Decision Trace Filters</h2>
    <div class="trace-controls">
      <input class="trace-search" id="traceSearch" placeholder="Filter trace messages (e.g. 110017, GIVE-BACK, LLM Response JSON)" />
      <button id="traceClear">Clear</button>
      <span style="font-size:11px;color:#6c7c99" id="traceCount"></span>
    </div>
    <div class="trace-controls" id="traceFilters"></div>
    <div class="trace-wrap">
      <table class="trace-table" id="traceTable">
        <thead><tr><th>Time</th><th>Type</th><th>Level</th><th>Component</th><th>Message</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const D = {json.dumps(data, default=str)};
const fmt = t => new Date(t+'Z').toLocaleTimeString('en-AU',{{hour:'2-digit',minute:'2-digit'}});
const fmtFull = t => new Date(t+'Z').toLocaleTimeString('en-AU',{{hour:'2-digit',minute:'2-digit',second:'2-digit'}});

// KPIs
(()=>{{
  const k = document.getElementById('kpis');
  const sigs = D.signals, fills = D.fills, port = D.portfolio, errs = D.errors;
  const buys = sigs.filter(s=>s.signal==='BUY').length, sells = sigs.filter(s=>s.signal==='SELL').length, holds = sigs.filter(s=>s.signal==='HOLD').length;
  const avgApi = sigs.length ? (sigs.reduce((a,s)=>a+s.api_time,0)/sigs.length).toFixed(1) : '-';
  const totalComm = fills.reduce((a,f)=>a+f.commission,0).toFixed(2);
  const eq = port.length ? port[port.length-1].equity.toFixed(0) : '-';
  const pnl = port.length ? port[port.length-1].last5_pnl.toFixed(2) : '-';
  const pnlClass = parseFloat(pnl)>=0?'green':'red';
  const items = [
    ['Total Signals', sigs.length, 'blue'], ['BUY / SELL / HOLD', `${{buys}}/${{sells}}/${{holds}}`, 'purple'],
    ['Avg API Latency', avgApi+'s', 'amber'], ['Order Fills', fills.length, 'blue'],
    ['Last5 PnL', '$'+pnl, pnlClass], ['Equity', '$'+Number(eq).toLocaleString(), 'green']
  ];
  k.innerHTML = items.map(([l,v,c])=>`<div class="kpi"><div class="label">${{l}}</div><div class="value ${{c}}">${{v}}</div></div>`).join('');
}})();

// Chart defaults
Chart.defaults.color = '#7b8ba5'; Chart.defaults.borderColor = '#1e2a42';
const timeX = {{type:'time',time:{{tooltipFormat:'HH:mm:ss'}},ticks:{{maxTicksLimit:12,font:{{size:10}}}}}};

// Price + signals chart
(()=>{{
  const ob = D.ob, sigs = D.signals, fills = D.fills;
  if(!ob.length) return;
  const prices = ob.map(o=>({{x:new Date(o.ts+'Z'),y:(o.bid+o.ask)/2}}));
  const buyPts = sigs.filter(s=>s.signal==='BUY').map(s=>({{x:new Date(s.ts+'Z'),y:ob.reduce((best,o)=>Math.abs(new Date(o.ts+'Z')-new Date(s.ts+'Z'))<Math.abs(new Date(best.ts+'Z')-new Date(s.ts+'Z'))?o:best).bid}}));
  const sellPts = sigs.filter(s=>s.signal==='SELL').map(s=>({{x:new Date(s.ts+'Z'),y:ob.reduce((best,o)=>Math.abs(new Date(o.ts+'Z')-new Date(s.ts+'Z'))<Math.abs(new Date(best.ts+'Z')-new Date(s.ts+'Z'))?o:best).ask}}));
  const fillBuy = fills.filter(f=>f.side==='BUY').map(f=>({{x:new Date(f.ts+'Z'),y:f.px}}));
  const fillSell = fills.filter(f=>f.side==='SELL').map(f=>({{x:new Date(f.ts+'Z'),y:f.px}}));
  new Chart(document.getElementById('priceChart'),{{type:'line',data:{{datasets:[
    {{label:'Mid Price',data:prices,borderColor:'#4fc3f7',borderWidth:1.5,pointRadius:0,fill:false,tension:0.1}},
    {{label:'BUY Signal',data:buyPts,type:'scatter',pointStyle:'triangle',pointRadius:8,backgroundColor:'#22c55e',borderColor:'#22c55e'}},
    {{label:'SELL Signal',data:sellPts,type:'scatter',pointStyle:'triangle',rotation:180,pointRadius:8,backgroundColor:'#ef4444',borderColor:'#ef4444'}},
    {{label:'Fill BUY',data:fillBuy,type:'scatter',pointStyle:'rect',pointRadius:6,backgroundColor:'#22c55e88'}},
    {{label:'Fill SELL',data:fillSell,type:'scatter',pointStyle:'rect',pointRadius:6,backgroundColor:'#ef444488'}}
  ]}},options:{{responsive:true,scales:{{x:timeX,y:{{ticks:{{font:{{size:10}}}}}}}},plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}}}});
}})();

// OB Microstructure
(()=>{{
  const ob = D.ob; if(!ob.length) return;
  new Chart(document.getElementById('obChart'),{{type:'line',data:{{datasets:[
    {{label:'OFI',data:ob.map(o=>({{x:new Date(o.ts+'Z'),y:o.ofi}})),borderColor:'#7c4dff',borderWidth:1.2,pointRadius:0,tension:0.2}},
    {{label:'Trade Flow',data:ob.map(o=>({{x:new Date(o.ts+'Z'),y:o.tf}})),borderColor:'#f59e0b',borderWidth:1.2,pointRadius:0,tension:0.2}},
    {{label:'Queue Pressure',data:ob.map(o=>({{x:new Date(o.ts+'Z'),y:o.qp}})),borderColor:'#22c55e',borderWidth:1,pointRadius:0,tension:0.2}}
  ]}},options:{{responsive:true,scales:{{x:timeX,y:{{ticks:{{font:{{size:10}}}}}}}},plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}}}});
}})();

// Equity
(()=>{{
  const p = D.portfolio; if(!p.length) return;
  new Chart(document.getElementById('eqChart'),{{type:'line',data:{{datasets:[
    {{label:'Equity',data:p.map(x=>({{x:new Date(x.ts+'Z'),y:x.equity}})),borderColor:'#22c55e',borderWidth:1.5,pointRadius:0,fill:{{target:'origin',above:'#22c55e11'}},tension:0.2}},
    {{label:'Available',data:p.map(x=>({{x:new Date(x.ts+'Z'),y:x.available}})),borderColor:'#3b82f6',borderWidth:1,pointRadius:0,tension:0.2}}
  ]}},options:{{responsive:true,scales:{{x:timeX,y:{{ticks:{{font:{{size:10}}}}}}}},plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}}}});
}})();

// Indicators
(()=>{{
  const ctx = D.llm_ctx; if(!ctx.length) return;
  new Chart(document.getElementById('indChart'),{{type:'line',data:{{datasets:[
    {{label:'RSI',data:ctx.map(c=>({{x:new Date(c.ts+'Z'),y:c.rsi}})),borderColor:'#f59e0b',borderWidth:1.5,pointRadius:0,yAxisID:'y'}},
    {{label:'RVOL',data:ctx.map(c=>({{x:new Date(c.ts+'Z'),y:c.rvol}})),borderColor:'#7c4dff',borderWidth:1.5,pointRadius:0,yAxisID:'y1'}}
  ]}},options:{{responsive:true,scales:{{x:timeX,y:{{position:'left',ticks:{{font:{{size:10}}}}}},y1:{{position:'right',grid:{{display:false}},ticks:{{font:{{size:10}}}}}}}},plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}}}});
}})();

// API Latency
(()=>{{
  const s = D.signals; if(!s.length) return;
  new Chart(document.getElementById('latChart'),{{type:'bar',data:{{datasets:[
    {{label:'API Time (s)',data:s.map(x=>({{x:new Date(x.ts+'Z'),y:x.api_time}})),backgroundColor:s.map(x=>x.api_time>15?'#ef444488':x.api_time>10?'#f59e0b88':'#22c55e88'),barThickness:6}}
  ]}},options:{{responsive:true,scales:{{x:timeX,y:{{ticks:{{font:{{size:10}}}}}}}},plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}}}});
}})();

// Sweep imbalance
(()=>{{
  const ob = D.ob; if(!ob.length) return;
  new Chart(document.getElementById('swChart'),{{type:'bar',data:{{datasets:[
    {{label:'Buy Sweeps',data:ob.filter((_,i)=>i%3===0).map(o=>({{x:new Date(o.ts+'Z'),y:o.sw_b}})),backgroundColor:'#22c55e44',barThickness:4}},
    {{label:'Sell Sweeps',data:ob.filter((_,i)=>i%3===0).map(o=>({{x:new Date(o.ts+'Z'),y:-o.sw_s}})),backgroundColor:'#ef444444',barThickness:4}}
  ]}},options:{{responsive:true,scales:{{x:timeX,y:{{ticks:{{font:{{size:10}}}}}}}},plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}}}});
}})();

// Signal table
(()=>{{
  const tb = document.querySelector('#sigTable tbody');
  D.signals.slice().reverse().forEach(s=>{{
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${{fmtFull(s.ts)}}</td><td><span class="badge ${{s.signal}}">${{s.signal}}</span></td><td><span class="badge ${{s.confidence}}">${{s.confidence}}</span></td><td>${{s.api_time}}</td><td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{s.reason}}">${{s.reason}}</td>`;
    tb.appendChild(tr);
  }});
}})();

// Error list
(()=>{{
  const el = document.getElementById('errList');
  D.errors.slice().reverse().forEach(e=>{{
    el.innerHTML += `<div class="err-item ${{e.level}}"><div class="ts">${{fmtFull(e.ts)}} [${{e.level}}]</div><div>${{e.msg}}</div></div>`;
  }});
}})();

// Fill table
(()=>{{
  const tb = document.querySelector('#fillTable tbody');
  D.fills.slice().reverse().forEach(f=>{{
    const cls = f.side==='BUY'?'color:#22c55e':'color:#ef4444';
    tb.appendChild(Object.assign(document.createElement('tr'),{{innerHTML:`<td>${{fmtFull(f.ts)}}</td><td style="${{cls}};font-weight:600">${{f.side}}</td><td>${{f.qty}}</td><td>${{f.px.toLocaleString()}}</td><td>${{f.commission.toFixed(4)}}</td>`}}));
  }});
}})();

// Trace filters + table
(()=>{{
  const all = (D.events || []).slice().sort((a,b)=>a.ts.localeCompare(b.ts));
  const types = Array.from(new Set(all.map(e => e.type))).sort();
  const selected = new Set(types);
  const filterWrap = document.getElementById('traceFilters');
  const searchInput = document.getElementById('traceSearch');
  const clearBtn = document.getElementById('traceClear');
  const tbody = document.querySelector('#traceTable tbody');
  const countEl = document.getElementById('traceCount');

  const renderRows = () => {{
    const q = (searchInput.value || '').toLowerCase().trim();
    const rows = all.filter(e => selected.has(e.type)).filter(e => {{
      if (!q) return true;
      return (e.msg || '').toLowerCase().includes(q) || (e.type || '').toLowerCase().includes(q);
    }});
    tbody.innerHTML = '';
    rows.slice().reverse().forEach(e => {{
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${{fmtFull(e.ts)}}</td><td>${{e.type}}</td><td>${{e.level || '-'}}</td><td>${{e.component || '-'}}</td><td style="max-width:760px;word-break:break-word">${{e.msg || ''}}</td>`;
      tbody.appendChild(tr);
    }});
    countEl.textContent = `Showing ${{rows.length}} / ${{all.length}} events`;
  }};

  types.forEach(t => {{
    const label = document.createElement('label');
    label.className = 'trace-filter';
    label.innerHTML = `<input type="checkbox" checked data-type="${{t}}" /> <span>${{t}}</span>`;
    label.querySelector('input').addEventListener('change', (ev) => {{
      if (ev.target.checked) selected.add(t); else selected.delete(t);
      renderRows();
    }});
    filterWrap.appendChild(label);
  }});

  searchInput.addEventListener('input', renderRows);
  clearBtn.addEventListener('click', () => {{
    searchInput.value = '';
    renderRows();
  }});
  renderRows();
}})();
</script></body></html>"""

if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else find_latest_log()
    if not log_path:
        print("No log file found"); sys.exit(1)
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    print(f"Parsing {log_path} (last {hours}h)...")
    data = parse_log(log_path, hours)
    print(f"  Signals: {len(data['signals'])}, OB snapshots: {len(data['ob'])}, "
          f"Portfolio: {len(data['portfolio'])}, Fills: {len(data['fills'])}, Errors: {len(data['errors'])}, Events: {len(data['events'])}")
    
    out = "logs/dashboard.html"
    with open(out, "w") as f:
        f.write(generate_html(data, log_path, hours))
    print(f"✅ Dashboard saved to {out}")
    print(f"   Open: file://{os.path.abspath(out)}")
