"""
Screener API Server
Run this with: python screener_server.py
Then open dashboard.html in your browser
"""

from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import requests
import time
import logging
import warnings
import os
from datetime import datetime, timedelta

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

app = Flask(__name__)

TREND_KEYWORDS = {
    "nuclear":        ["Nuclear power"],
    "hydrogen":       ["Hydrogen power"],
    "fuel cell":      ["Hydrogen power"],
    "grid":           ["Grid infrastructure"],
    "energy storage": ["Energy storage"],
    "charging":       ["EV charging"],
    "electric vehic": ["EV"],
    "water":          ["Water infrastructure"],
    "semiconductor":  ["Semiconductor fab"],
    "defense":        ["Defence tech"],
    "drone":          ["Defence tech"],
    "lithium":        ["Critical minerals"],
    "rare earth":     ["Critical minerals"],
    "uranium":        ["Nuclear power"],
}

def detect_trends(desc):
    found = []
    for kw, trends in TREND_KEYWORDS.items():
        if kw in desc:
            for t in trends:
                if t not in found:
                    found.append(t)
    return found

def pct_above_low(price, low):
    if not price or not low or low == 0:
        return None
    return round(((price - low) / low) * 100, 1)

def get_hist(sym):
    try:
        df = yf.download(sym, period="60d", interval="1d",
                         progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None

def calc_squeeze(df, window=20):
    if df is None or len(df) < window + 5:
        return None
    try:
        close = df["Close"]
        ma  = close.rolling(window).mean()
        std = close.rolling(window).std()
        bw  = ((ma + 2*std) - (ma - 2*std)) / ma
        rng = bw.rolling(60).max() - bw.rolling(60).min()
        cur = bw.iloc[-1]
        mn  = bw.rolling(60).min().iloc[-1]
        if rng.iloc[-1] == 0:
            return 50
        return max(0, min(100, round((1 - (cur - mn) / rng.iloc[-1]) * 100)))
    except Exception:
        return None

def calc_rvol(df):
    if df is None or len(df) < 12:
        return None
    try:
        vols = df["Volume"].dropna()
        avg  = vols.iloc[-11:-1].mean()
        if avg == 0:
            return None
        return round(float(vols.iloc[-1] / avg), 2)
    except Exception:
        return None

def check_insider(sym):
    try:
        start = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
        end   = datetime.now().strftime('%Y-%m-%d')
        r = requests.get(
            f"https://efts.sec.gov/LATEST/search-index?"
            f"q=%22{sym}%22&dateRange=custom&startdt={start}&enddt={end}&forms=4",
            timeout=5,
            headers={"User-Agent": "screener research@example.com"}
        )
        if r.status_code == 200:
            hits = r.json().get("hits", {}).get("total", {})
            return hits.get("value", 0) if isinstance(hits, dict) else int(hits)
        return 0
    except Exception:
        return 0

def score_s1(sym):
    try:
        info  = yf.Ticker(sym).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None
        low_52w = info.get("fiftyTwoWeekLow")
        mcap    = info.get("marketCap") or 0
        si      = (info.get("shortPercentOfFloat") or 0) * 100
        n       = info.get("numberOfAnalystOpinions")
        ni      = info.get("netIncomeToCommon")
        rev     = info.get("totalRevenue")
        pt      = info.get("targetMeanPrice")
        desc    = (info.get("longBusinessSummary") or "").lower()
        trends  = detect_trends(desc)
        pct     = pct_above_low(price, low_52w)
        s_low = (30 if pct and pct<=10 else 25 if pct and pct<=25 else
                 18 if pct and pct<=50 else 10 if pct and pct<=100 else 3) if pct else 0
        s_si  = (20 if si>=30 else 16 if si>=20 else 10 if si>=10 else 5 if si>=5 else 0)
        s_cov = (8 if n is None else 15 if n<=2 else 13 if n<=4 else
                 10 if n<=6 else 6 if n<=10 else 2)
        s_tr  = (20 if len(trends)>=2 else 12 if len(trends)==1 else 0)
        s_lo  = 0
        if rev and rev > 0:
            if ni is None:   s_lo = 5
            elif ni < 0:
                r2 = abs(ni) / rev
                s_lo = (15 if r2<0.1 else 12 if r2<0.3 else 8 if r2<0.7 else 4)
            else:            s_lo = 5
        total = s_low + s_si + s_cov + s_tr + s_lo
        pt_up = round(((pt-price)/price*100),1) if pt and price else None
        return {
            "sym": sym, "name": info.get("shortName", sym),
            "price": round(price, 2), "mcap_M": round(mcap/1_000_000),
            "pct_low": pct, "si_pct": round(si, 1),
            "analysts": n or 0, "pt_up": pt_up,
            "trends": trends, "total": total,
            "s_low": s_low, "s_si": s_si, "s_cov": s_cov,
            "s_tr": s_tr, "s_lo": s_lo,
            "setup": ("PRIME" if total>=70 else "Watch" if total>=55 else
                      "Building" if total>=40 else "Early")
        }
    except Exception:
        return None

def score_s2(sym):
    try:
        info  = yf.Ticker(sym).info
        df    = get_hist(sym)
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None
        mcap   = info.get("marketCap") or 0
        fl     = (info.get("floatShares") or 0) / 1_000_000
        si     = (info.get("shortPercentOfFloat") or 0) * 100
        prev   = info.get("previousClose")
        sq     = calc_squeeze(df)
        rv     = calc_rvol(df)
        ins    = check_insider(sym)
        s_sq = (20 if sq and sq>=85 else 16 if sq and sq>=70 else
                11 if sq and sq>=55 else 6 if sq and sq>=40 else 0)
        s_rv = (20 if rv and rv>=10 else 16 if rv and rv>=5 else
                12 if rv and rv>=3 else 7 if rv and rv>=2 else
                4 if rv and rv>=1.5 else 0)
        s_si = (20 if si>=30 else 16 if si>=20 else 11 if si>=15 else
                7 if si>=10 else 3 if si>=5 else 0)
        s_fl = (20 if fl<=5 else 17 if fl<=10 else 13 if fl<=20 else
                8 if fl<=50 else 3) if fl else 5
        s_in = (15 if ins>=3 else 10 if ins>=1 else 0)
        total = s_sq + s_rv + s_si + s_fl + s_in
        chg   = round(((price-prev)/prev*100),2) if prev else 0
        return {
            "sym": sym, "name": info.get("shortName", sym),
            "price": round(price, 2), "chg": chg,
            "mcap_M": round(mcap/1_000_000),
            "float_m": round(fl, 1), "si_pct": round(si, 1),
            "rvol": rv or 0, "squeeze": sq or 0, "insider": ins,
            "total": total,
            "s_sq": s_sq, "s_rv": s_rv, "s_si": s_si,
            "s_fl": s_fl, "s_in": s_in,
            "setup": ("IMMINENT" if total>=65 else "Watch" if total>=50 else
                      "Building" if total>=35 else "Early")
        }
    except Exception:
        return None

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Screener</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap');
  :root{--bg:#080b0f;--surface:#0d1117;--card:#111820;--border:#1e2d3d;--accent:#00d4aa;--accent2:#ff6b35;--gold:#f0b429;--red:#ff4444;--blue:#4a9eff;--text:#e8edf2;--muted:#5a7a8a}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh}
  body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(0,212,170,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(0,212,170,.025) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0}
  .app{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:24px 20px}
  .header{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:12px}
  .logo{display:flex;align-items:center;gap:14px}
  .logo-icon{width:44px;height:44px;background:linear-gradient(135deg,var(--accent),#007a60);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px}
  .logo h1{font-size:20px;font-weight:800}
  .logo p{font-size:11px;color:var(--muted);font-family:'Space Mono',monospace;margin-top:2px}
  .header-right{display:flex;align-items:center;gap:12px}
  .pulse-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.8)}}
  .clock{font-family:'Space Mono',monospace;font-size:12px;color:var(--muted)}
  .tabs{display:flex;gap:2px;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:4px;width:fit-content;margin-bottom:20px}
  .tab{padding:9px 22px;border-radius:9px;border:none;background:transparent;color:var(--muted);font-family:'Syne',sans-serif;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}
  .tab.active{background:var(--card);color:var(--text)}
  .tab:hover:not(.active){color:var(--text)}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px;margin-bottom:20px}
  .panel h3{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:12px}
  .input-row{display:flex;gap:10px}
  input[type=text]{flex:1;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:11px 14px;color:var(--text);font-family:'Space Mono',monospace;font-size:12px;outline:none;transition:border-color .2s}
  input[type=text]:focus{border-color:var(--accent)}
  input[type=text]::placeholder{color:var(--muted)}
  .btn{background:var(--accent);color:#000;border:none;border-radius:10px;padding:11px 24px;font-family:'Syne',sans-serif;font-size:13px;font-weight:700;cursor:pointer;transition:all .2s;white-space:nowrap}
  .btn:hover{background:#00f0c0;transform:translateY(-1px)}
  .btn:disabled{background:var(--muted);cursor:not-allowed;transform:none}
  .hint{margin-top:8px;font-size:11px;color:var(--muted);font-family:'Space Mono',monospace}
  .hint a{color:var(--accent);text-decoration:none}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:20px}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px}
  .stat-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:5px}
  .stat-val{font-size:24px;font-weight:800;font-family:'Space Mono',monospace}
  .prog-wrap{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:20px;display:none}
  .prog-label{font-size:12px;color:var(--muted);font-family:'Space Mono',monospace;margin-bottom:10px}
  .prog-bg{background:var(--card);border-radius:99px;height:5px;overflow:hidden}
  .prog-fill{height:100%;background:linear-gradient(90deg,var(--accent),#007a60);border-radius:99px;transition:width .3s;width:0%}
  .results-wrap{background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden}
  .results-hd{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border)}
  .results-title{font-size:14px;font-weight:700}
  .results-count{font-family:'Space Mono',monospace;font-size:11px;color:var(--muted)}
  .tscroll{overflow-x:auto}
  table{width:100%;border-collapse:collapse;font-size:12px}
  thead th{background:var(--card);padding:11px 14px;text-align:left;font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}
  thead th:hover{color:var(--text)}
  tbody tr{border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s}
  tbody tr:last-child{border-bottom:none}
  tbody tr:hover{background:var(--card)}
  tbody td{padding:12px 14px;white-space:nowrap;vertical-align:middle}
  .sym-name{font-family:'Space Mono',monospace;font-size:13px;font-weight:700}
  .co-name{font-size:10px;color:var(--muted);margin-top:1px;max-width:140px;overflow:hidden;text-overflow:ellipsis}
  .mono{font-family:'Space Mono',monospace}
  .green{color:var(--accent)}.gold{color:var(--gold)}.red{color:var(--red)}.blue{color:var(--blue)}.muted{color:var(--muted)}.orange{color:var(--accent2)}
  .score-wrap{display:flex;align-items:center;gap:8px}
  .score-num{font-family:'Space Mono',monospace;font-size:16px;font-weight:700;min-width:28px}
  .score-bg{width:48px;height:3px;background:var(--border);border-radius:99px;overflow:hidden}
  .score-fill{height:100%;border-radius:99px}
  .badge{display:inline-block;padding:2px 9px;border-radius:99px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}
  .b-prime{background:rgba(0,212,170,.15);color:var(--accent);border:1px solid rgba(0,212,170,.3)}
  .b-watch{background:rgba(240,180,41,.15);color:var(--gold);border:1px solid rgba(240,180,41,.3)}
  .b-building{background:rgba(74,158,255,.15);color:var(--blue);border:1px solid rgba(74,158,255,.3)}
  .b-early{background:rgba(90,122,138,.1);color:var(--muted);border:1px solid var(--border)}
  .b-imminent{background:rgba(255,107,53,.15);color:var(--accent2);border:1px solid rgba(255,107,53,.3)}
  .trend-tag{display:inline-block;padding:1px 7px;background:rgba(0,212,170,.07);border:1px solid rgba(0,212,170,.2);border-radius:4px;font-size:10px;color:var(--accent);margin:1px}
  .empty{padding:56px 20px;text-align:center}
  .empty-icon{font-size:40px;opacity:.3;margin-bottom:12px}
  .empty-title{font-size:16px;font-weight:700;color:var(--muted);margin-bottom:6px}
  .empty-sub{font-size:12px;color:var(--muted);opacity:.7}
  .det-td{background:rgba(0,212,170,.02);border-top:1px solid rgba(0,212,170,.1);padding:14px 20px !important}
  .bk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px}
  .bk-item{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:9px 12px}
  .bk-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
  .bk-val{font-family:'Space Mono',monospace;font-size:12px;font-weight:700}
  .bk-bar-bg{height:3px;background:var(--border);border-radius:99px;margin-top:5px;overflow:hidden}
  .bk-bar-fill{height:100%;border-radius:99px;background:var(--accent)}
  @media(max-width:600px){.header{flex-direction:column;align-items:flex-start}.input-row{flex-direction:column}.btn{width:100%}}
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <div class="logo">
      <div class="logo-icon">📈</div>
      <div><h1>Stock Screener</h1><p>Pre-Parabolic DNA + Microcap Pre-Pop</p></div>
    </div>
    <div class="header-right">
      <div class="pulse-dot"></div>
      <div class="clock" id="clock"></div>
    </div>
  </div>
  <div class="tabs">
    <button class="tab active" onclick="setMode('1',this)">Screener 1 — Pre-Parabolic</button>
    <button class="tab" onclick="setMode('2',this)">Screener 2 — Microcap Pre-Pop</button>
  </div>
  <div class="panel">
    <h3 id="panel-title">Pre-Parabolic DNA + Trend Scanner</h3>
    <div class="input-row">
      <input type="text" id="ticker-input" placeholder="CHPT, PLUG, FCEL, LCID, SMR..."/>
      <button class="btn" id="scan-btn" onclick="runScan()">Run Scan</button>
    </div>
    <p class="hint" id="hint">Get tickers from <a href="https://finviz.com/screener.ashx" target="_blank">Finviz</a> → Filters: Mcap &lt;$2B, Price $0.50-$25, Short Float &gt;10%, Perf 52W Down</p>
  </div>
  <div class="stats">
    <div class="stat"><div class="stat-label">Scanned</div><div class="stat-val" id="s-scan">-</div></div>
    <div class="stat"><div class="stat-label">Results</div><div class="stat-val" id="s-res">-</div></div>
    <div class="stat"><div class="stat-label" id="s-top-lbl">Prime</div><div class="stat-val green" id="s-top">-</div></div>
    <div class="stat"><div class="stat-label">Watch</div><div class="stat-val gold" id="s-watch">-</div></div>
    <div class="stat"><div class="stat-label">Last Scan</div><div class="stat-val mono" style="font-size:15px" id="s-time">-</div></div>
  </div>
  <div class="prog-wrap" id="prog-wrap">
    <div class="prog-label" id="prog-label">Scanning...</div>
    <div class="prog-bg"><div class="prog-fill" id="prog-fill"></div></div>
  </div>
  <div class="results-wrap">
    <div class="results-hd">
      <div class="results-title" id="res-title">Results</div>
      <div class="results-count" id="res-count"></div>
    </div>
    <div class="tscroll"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
    <div class="empty" id="empty">
      <div class="empty-icon">🔍</div>
      <div class="empty-title">No results yet</div>
      <div class="empty-sub">Paste tickers above and click Run Scan</div>
    </div>
  </div>
</div>
<script>
let mode="1",results=[],sortK="total",sortD=-1,openTr=null;
setInterval(()=>{document.getElementById("clock").textContent=new Date().toLocaleTimeString("en-GB");},1000);
function setMode(m,el){mode=m;document.querySelectorAll(".tab").forEach(t=>t.classList.remove("active"));el.classList.add("active");if(m==="1"){document.getElementById("panel-title").textContent="Pre-Parabolic DNA + Trend Scanner";document.getElementById("hint").innerHTML='Get tickers from <a href="https://finviz.com/screener.ashx" target="_blank">Finviz</a> → Filters: Mcap &lt;$2B, Price $0.50-$25, Short Float &gt;10%, Perf 52W Down';document.getElementById("s-top-lbl").textContent="Prime";}else{document.getElementById("panel-title").textContent="Microcap Pre-Pop Hunter";document.getElementById("hint").innerHTML='Get tickers from <a href="https://finviz.com/screener.ashx" target="_blank">Finviz</a> → Filters: Mcap &lt;$300M, Float &lt;20M, Short Float &gt;10%, Rel Vol &gt;1.5';document.getElementById("s-top-lbl").textContent="Imminent";}clearAll();}
function clearAll(){results=[];document.getElementById("tbody").innerHTML="";document.getElementById("empty").style.display="block";document.getElementById("res-count").textContent="";["s-scan","s-res","s-top","s-watch","s-time"].forEach(id=>document.getElementById(id).textContent="-");}
function sc(s){return s>=70?"#00d4aa":s>=55?"#f0b429":s>=40?"#4a9eff":"#5a7a8a"}
function badge(setup){const m={PRIME:"b-prime",Watch:"b-watch",Building:"b-building",Early:"b-early",IMMINENT:"b-imminent"};return `<span class="badge ${m[setup]||'b-early'}">${setup}</span>`;}
function buildHead1(){document.getElementById("thead").innerHTML=`<tr><th onclick="sort('sym')">Ticker</th><th onclick="sort('price')">Price</th><th onclick="sort('mcap_M')">Mcap</th><th onclick="sort('pct_low')">From Low</th><th onclick="sort('si_pct')">Short%</th><th onclick="sort('analysts')">Analysts</th><th onclick="sort('pt_up')">PT%</th><th>Trend</th><th onclick="sort('total')">Score</th><th>Setup</th></tr>`;}
function buildHead2(){document.getElementById("thead").innerHTML=`<tr><th onclick="sort('sym')">Ticker</th><th onclick="sort('price')">Price</th><th onclick="sort('chg')">Chg%</th><th onclick="sort('mcap_M')">Mcap</th><th onclick="sort('float_m')">Float</th><th onclick="sort('si_pct')">Short%</th><th onclick="sort('rvol')">RVol</th><th onclick="sort('squeeze')">Squeeze</th><th onclick="sort('insider')">Insider</th><th onclick="sort('total')">Score</th><th>Setup</th></tr>`;}
function renderRows1(data){const tb=document.getElementById("tbody");tb.innerHTML="";data.forEach((r)=>{const c=sc(r.total);const trends=(r.trends||[]).map(t=>`<span class="trend-tag">${t}</span>`).join("")||"-";const tr=document.createElement("tr");tr.onclick=()=>toggleDet(tr,r);tr.innerHTML=`<td><div class="sym-name">${r.sym}</div><div class="co-name">${r.name}</div></td><td class="mono">$${r.price}</td><td class="mono muted">$${(r.mcap_M||0).toLocaleString()}M</td><td class="mono ${r.pct_low<=25?'green':r.pct_low<=60?'gold':'red'}">${r.pct_low!=null?'+'+r.pct_low+'%':'?'}</td><td class="mono ${r.si_pct>=20?'red':r.si_pct>=10?'gold':''}">${r.si_pct}%</td><td class="mono muted">${r.analysts}</td><td class="mono ${r.pt_up>30?'green':''}">${r.pt_up&&r.pt_up>0?'+'+r.pt_up+'%':'-'}</td><td>${trends}</td><td><div class="score-wrap"><span class="score-num" style="color:${c}">${r.total}</span><div class="score-bg"><div class="score-fill" style="width:${r.total}%;background:${c}"></div></div></div></td><td>${badge(r.setup)}</td>`;tb.appendChild(tr);});}
function renderRows2(data){const tb=document.getElementById("tbody");tb.innerHTML="";data.forEach((r)=>{const c=sc(r.total);const tr=document.createElement("tr");tr.onclick=()=>toggleDet(tr,r);tr.innerHTML=`<td><div class="sym-name">${r.sym}</div><div class="co-name">${r.name}</div></td><td class="mono">$${r.price}</td><td class="mono ${r.chg>=0?'green':'red'}">${r.chg!=null?(r.chg>=0?'+':'')+r.chg+'%':'?'}</td><td class="mono muted">$${(r.mcap_M||0).toLocaleString()}M</td><td class="mono muted">${r.float_m?r.float_m+'M':'?'}</td><td class="mono ${r.si_pct>=20?'red':r.si_pct>=10?'gold':''}">${r.si_pct}%</td><td class="mono ${r.rvol>=3?'green':''}">${r.rvol?r.rvol+'x':'?'}</td><td class="mono ${r.squeeze>=70?'green':''}">${r.squeeze||'?'}</td><td class="mono ${r.insider>0?'orange':'muted'}">${r.insider||'-'}</td><td><div class="score-wrap"><span class="score-num" style="color:${c}">${r.total}</span><div class="score-bg"><div class="score-fill" style="width:${r.total/0.75}%;background:${c}"></div></div></div></td><td>${badge(r.setup)}</td>`;tb.appendChild(tr);});}
function toggleDet(tr,r){const ex=document.getElementById("det-row");if(ex){ex.remove();if(openTr===tr){openTr=null;return;}}openTr=tr;const det=document.createElement("tr");det.id="det-row";const items=mode==="1"?[["Near 52w low",r.s_low,30],["Short interest",r.s_si,20],["Analyst cov",r.s_cov,15],["Trend align",r.s_tr,20],["Loss trajectory",r.s_lo,15]]:[["Bollinger squeeze",r.s_sq,20],["Relative volume",r.s_rv,20],["Short interest",r.s_si,20],["Float",r.s_fl,20],["Insider Form 4",r.s_in,15]];const cols=mode==="1"?10:11;det.innerHTML=`<td colspan="${cols}" class="det-td"><div class="bk-grid">${items.map(([l,s,m])=>`<div class="bk-item"><div class="bk-label">${l}</div><div class="bk-val">${s} / ${m}</div><div class="bk-bar-bg"><div class="bk-bar-fill" style="width:${Math.round(s/m*100)}%"></div></div></div>`).join("")}</div></td>`;tr.after(det);}
function sort(key){if(sortK===key)sortD*=-1;else{sortK=key;sortD=-1;}const sorted=[...results].sort((a,b)=>{const av=a[key]??-999,bv=b[key]??-999;return typeof av==="string"?sortD*av.localeCompare(bv):sortD*(av-bv);});mode==="1"?renderRows1(sorted):renderRows2(sorted);}
async function runScan(){const val=document.getElementById("ticker-input").value.trim();if(!val)return;const tickers=val.split(",").map(t=>t.trim()).filter(Boolean);const btn=document.getElementById("scan-btn");btn.disabled=true;btn.textContent="Scanning...";document.getElementById("empty").style.display="none";document.getElementById("prog-wrap").style.display="block";document.getElementById("prog-label").textContent=`Scanning ${tickers.length} tickers - please wait...`;let p=0;const fill=document.getElementById("prog-fill");const iv=setInterval(()=>{p=Math.min(p+100/(tickers.length*6),88);fill.style.width=p+"%";},600);try{const res=await fetch("/scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tickers:val,mode})});if(!res.ok)throw new Error("err");const data=await res.json();clearInterval(iv);fill.style.width="100%";setTimeout(()=>{document.getElementById("prog-wrap").style.display="none";fill.style.width="0%";},400);results=data.results||[];const top=results.filter(r=>r.setup===(mode==="1"?"PRIME":"IMMINENT")).length;const watch=results.filter(r=>r.setup==="Watch").length;document.getElementById("s-scan").textContent=data.scanned;document.getElementById("s-res").textContent=results.length;document.getElementById("s-top").textContent=top;document.getElementById("s-watch").textContent=watch;document.getElementById("s-time").textContent=data.timestamp;document.getElementById("res-title").textContent=mode==="1"?"Pre-Parabolic Results":"Microcap Pre-Pop Results";document.getElementById("res-count").textContent=`${results.length} stocks scored`;if(results.length===0){document.getElementById("empty").style.display="block";}else{document.getElementById("empty").style.display="none";mode==="1"?(buildHead1(),renderRows1(results)):(buildHead2(),renderRows2(results));}}catch(e){clearInterval(iv);document.getElementById("prog-wrap").style.display="none";alert("Scan failed. Please try again.");}btn.disabled=false;btn.textContent="Run Scan";}
document.getElementById("ticker-input").addEventListener("keydown",e=>{if(e.key==="Enter")runScan();});
</script>
</body>
</html>"""

@app.route("/")
def index():
    return HTML

@app.route("/scan", methods=["POST"])
def scan():
    data    = request.json
    tickers = [t.strip().upper() for t in data.get("tickers","").split(",") if t.strip()]
    mode    = data.get("mode", "1")
    results = []
    for sym in tickers[:50]:
        r = score_s1(sym) if mode == "1" else score_s2(sym)
        if r and r["total"] >= 20:
            results.append(r)
        time.sleep(0.3)
    results.sort(key=lambda x: x["total"], reverse=True)
    return jsonify({
        "results":   results,
        "scanned":   len(tickers),
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)