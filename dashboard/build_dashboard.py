"""Swing paper-trading dashboard generator — DUAL ENGINE (monthly + weekly).

Pulls BOTH live ledgers from the Oracle VM (best-effort scp), reads them,
computes metrics, overlays a NIFTY benchmark, and writes a SINGLE
self-contained ``swing_dashboard.html`` showing the two bots side-by-side:

    🟦 MONTHLY engine  (cyan)    -> paper_ledger.db
    🟪 WEEKLY  engine  (magenta) -> paper_ledger_weekly.db
    ⬜ NIFTY 50 benchmark (grey dashed) -> "are we beating doing nothing?"

Metrics per engine: equity, return, cash, invested, holdings, max-DD, win
rate, alpha vs Nifty, best/worst/avg-win/avg-loss/profit-factor, sector
exposure. A head-to-head chart overlays both engines + the benchmark.

Why a generated static page (not a live web server): the 1 GB VM freezes
under heavyweight processes, so we keep it doing ONLY the once-a-day runs.

Run:  python dashboard/build_dashboard.py   (the .bat does this + opens it)
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent

# Sector map (best-effort; falls back to "Other" if the project import fails).
try:
    sys.path.insert(0, str(PROJECT_ROOT))
    from data.universe import get_sector  # type: ignore
except Exception:  # noqa: BLE001
    def get_sector(symbol: str) -> str:  # type: ignore
        return "Other"

# ── Config ────────────────────────────────────────────────────────────
SSH_KEY = r"D:\Projects\AI Stock Market Analyzer\ssh-key-2026-05-06 (1).key"
VM_HOST = "opc@161.118.180.114"
INITIAL_CAPITAL = 500_000.0

ENGINES = [
    {"key": "monthly", "label": "MONTHLY ENGINE", "icon": "🟦", "color": "#22d3ee",
     "remote": "/opt/swing/paper_ledger.db"},
    {"key": "weekly", "label": "WEEKLY ENGINE", "icon": "🟪", "color": "#e879f9",
     "remote": "/opt/swing/paper_ledger_weekly.db"},
]
OUT_HTML = HERE / "swing_dashboard.html"


# ── Pull a ledger from the VM (best-effort) ───────────────────────────
def refresh(remote: str, local: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["scp", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ConnectTimeout=20", "-o", "BatchMode=yes",
             f"{VM_HOST}:{remote}", str(local)],
            capture_output=True, text=True, timeout=90)
        if r.returncode == 0:
            return True, "live"
        if "No such file" in (r.stderr or ""):
            return False, "no run yet"
        return False, "VM unreachable"
    except Exception:  # noqa: BLE001
        return False, "scp failed"


# ── Read a ledger ─────────────────────────────────────────────────────
def read_ledger(db: Path) -> dict:
    if not db.exists():
        return {"exists": False}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: dict = {"exists": True}
    try:
        t = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out["equity"] = [dict(r) for r in con.execute(
            "SELECT run_date, equity, cash, mtm FROM equity_curve "
            "ORDER BY run_date ASC")] if "equity_curve" in t else []
        out["positions"] = [dict(r) for r in con.execute(
            "SELECT symbol, entry_date, entry_price, shares, last_close, "
            "cost_basis FROM positions ORDER BY symbol")] if "positions" in t else []
        out["trades"] = [dict(r) for r in con.execute(
            "SELECT symbol, entry_date, exit_date, entry_price, exit_price, "
            "shares, pnl, return_pct, exit_reason FROM trades "
            "ORDER BY exit_date DESC, id DESC")] if "trades" in t else []
        out["runs"] = [dict(r) for r in con.execute(
            "SELECT run_date, status, n_orders, equity, error_message "
            "FROM runs ORDER BY run_date DESC LIMIT 15")] if "runs" in t else []
        row = con.execute("SELECT cash FROM cash_state WHERE id=1").fetchone() \
            if "cash_state" in t else None
        out["cash"] = float(row["cash"]) if row else INITIAL_CAPITAL
    finally:
        con.close()
    return out


# ── Nifty benchmark (yfinance, lazy + fail-safe) ──────────────────────
def fetch_nifty(start: str, end: str) -> list[dict]:
    """Daily ^NSEI closes between start/end (ISO dates), normalised to
    INITIAL_CAPITAL at the first point. Returns [] on any failure."""
    try:
        import yfinance as yf  # lazy
        import pandas as pd
        df = yf.download("^NSEI", start=start, end=pd.Timestamp(end) +
                         pd.Timedelta(days=2), progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        close = df["Close"].dropna()
        if hasattr(close, "columns"):       # MultiIndex guard
            close = close.iloc[:, 0]
        base = float(close.iloc[0])
        return [{"d": ts.date().isoformat(),
                 "e": round(INITIAL_CAPITAL * float(v) / base, 0)}
                for ts, v in close.items()]
    except Exception:  # noqa: BLE001
        return []


def nifty_return_over(bench: list[dict], dates: list[str]) -> float | None:
    """% return of the benchmark between the first and last of `dates`."""
    if not bench or not dates:
        return None
    by = {b["d"]: b["e"] for b in bench}
    span = [by[d] for d in dates if d in by]
    if len(span) < 2 or span[0] == 0:
        return None
    return round((span[-1] - span[0]) / span[0] * 100, 2)


# ── Market regime (Nifty level + 200-DMA bull/bear) ───────────────────
def fetch_market_regime() -> dict:
    """Current Nifty 50 level and 200-DMA regime. Independent of the
    ledgers, so it ALWAYS populates (immediate value). {} on failure."""
    try:
        import yfinance as yf  # lazy
        df = yf.download("^NSEI", period="400d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        close = df["Close"].dropna()
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        if len(close) < 50:
            return {}
        level = float(close.iloc[-1])
        dma = float(close.tail(200).mean())
        prev = float(close.iloc[-2]) if len(close) > 1 else level
        d20 = float(close.iloc[-21]) if len(close) > 21 else level
        return {
            "level": round(level, 0), "dma200": round(dma, 0),
            "pct_vs_dma": round((level - dma) / dma * 100, 2),
            "day_change_pct": round((level - prev) / prev * 100, 2),
            "mom20_pct": round((level - d20) / d20 * 100, 2),
            "regime": "BULLISH" if level >= dma else "BEARISH",
        }
    except Exception:  # noqa: BLE001
        return {}


# ── Compute metrics ───────────────────────────────────────────────────
def compute(L: dict) -> dict:
    eq = L.get("equity", [])
    positions = L.get("positions", [])
    trades = L.get("trades", [])
    cash = L.get("cash", INITIAL_CAPITAL)

    holdings, mtm = [], 0.0
    sectors: dict[str, dict] = {}
    for p in positions:
        val = p["shares"] * (p["last_close"] or p["entry_price"])
        cost = p.get("cost_basis") or (p["shares"] * p["entry_price"])
        upl = val - cost
        holdings.append({
            "symbol": p["symbol"], "entry_date": p["entry_date"],
            "shares": p["shares"], "entry_price": round(p["entry_price"], 2),
            "last_close": round(p["last_close"] or p["entry_price"], 2),
            "value": round(val, 0), "upl_pct": round((upl / cost * 100) if cost else 0, 2),
        })
        mtm += val
        sec = get_sector(p["symbol"]).split(".")[0]
        b = sectors.setdefault(sec, {"sector": sec, "n": 0, "value": 0.0})
        b["n"] += 1
        b["value"] += val

    equity = (eq[-1]["equity"] if eq else cash + mtm)
    total_ret = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    peak, max_dd = -1e18, 0.0
    for r in eq:
        peak = max(peak, r["equity"])
        if peak > 0:
            max_dd = min(max_dd, (r["equity"] - peak) / peak * 100)

    wins = [x for x in trades if x["pnl"] > 0]
    losses = [x for x in trades if x["pnl"] < 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else None
    avg_win = (sum(x["return_pct"] for x in wins) / len(wins)) if wins else None
    avg_loss = (sum(x["return_pct"] for x in losses) / len(losses)) if losses else None
    gross_win = sum(x["pnl"] for x in wins)
    gross_loss = abs(sum(x["pnl"] for x in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (None if not wins else float("inf"))
    best = max((x["return_pct"] for x in trades), default=None)
    worst = min((x["return_pct"] for x in trades), default=None)

    sector_list = sorted(({"sector": s["sector"], "n": s["n"],
                           "value": round(s["value"], 0)} for s in sectors.values()),
                         key=lambda x: -x["value"])

    return {
        "exists": L.get("exists", False),
        "equity": round(equity, 0), "cash": round(cash, 0), "invested": round(mtm, 0),
        "total_ret_pct": round(total_ret, 2), "max_dd_pct": round(max_dd, 2),
        "n_holdings": len(holdings), "n_trades": len(trades),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "profit_factor": (round(pf, 2) if isinstance(pf, float) and pf != float("inf")
                          else ("∞" if pf == float("inf") else None)),
        "best": round(best, 2) if best is not None else None,
        "worst": round(worst, 2) if worst is not None else None,
        "alpha_vs_nifty": None,  # filled in main() once benchmark known
        "holdings": holdings, "trades": trades[:40], "runs": L.get("runs", []),
        "sectors": sector_list,
        "curve": [{"d": r["run_date"], "e": round(r["equity"], 0)} for r in eq],
        "dates": [r["run_date"] for r in eq],
    }


# ── Render ────────────────────────────────────────────────────────────
def render(engines_data: list, benchmark: list, regime: dict) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    any_data = any(e["metrics"]["curve"] for e in engines_data)
    empty = "" if any_data else (
        '<div class="banner">⚠ <b>AWAITING FIRST SIGNALS.</b> Both engines start '
        '<b>Mon 8 Jun, 15:45/15:50 IST</b>. Monthly first buys at month-start (~1 Jul); '
        'weekly buys from its first week. The Nifty benchmark line appears once data flows. '
        'Until then both hold flat at ₹5,00,000 — nominal, not a fault.</div>')
    payload = json.dumps({
        "now": now, "benchmark": benchmark, "regime": regime,
        "engines": [{"key": e["key"], "label": e["label"], "icon": e["icon"],
                     "color": e["color"], "note": e["note"], "m": e["metrics"]}
                    for e in engines_data],
    })
    return _TEMPLATE.replace("__PAYLOAD__", payload).replace("__EMPTY__", empty)


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SWING · DUAL-ENGINE DECK</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@500;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{--cyan:#22d3ee;--mag:#e879f9;--grn:#34d399;--red:#fb7185;--ink:#dbeafe;--mut:#6b7a99;
        --glass:rgba(18,28,46,.55);--line:rgba(80,120,200,.18);}
  *{box-sizing:border-box} html,body{margin:0}
  body{color:var(--ink);font-family:Rajdhani,system-ui,sans-serif;padding:24px;
    background:radial-gradient(1200px 700px at 12% -10%,rgba(34,211,238,.12),transparent 60%),
               radial-gradient(1000px 700px at 110% 0%,rgba(232,121,249,.12),transparent 55%),#05070e;}
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
    background-image:linear-gradient(rgba(60,110,200,.08) 1px,transparent 1px),linear-gradient(90deg,rgba(60,110,200,.08) 1px,transparent 1px);
    background-size:44px 44px;mask:radial-gradient(circle at 50% 30%,#000 0%,transparent 78%);}
  .wrap{position:relative;z-index:1;max-width:1180px;margin:0 auto}
  h1{font-family:Orbitron;font-weight:900;font-size:24px;letter-spacing:3px;margin:0;
     background:linear-gradient(90deg,var(--cyan),var(--mag));-webkit-background-clip:text;background-clip:text;color:transparent}
  .sub{font-family:Share Tech Mono;color:var(--mut);font-size:12px;letter-spacing:1px;margin:4px 0 18px}
  .banner{background:linear-gradient(90deg,rgba(232,121,249,.10),rgba(34,211,238,.06));border:1px solid var(--line);
    border-left:3px solid var(--mag);border-radius:12px;padding:12px 16px;margin-bottom:20px;font-size:15px;line-height:1.55;backdrop-filter:blur(8px)}
  .vs{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px}
  .vscard{background:var(--glass);border:1px solid var(--line);border-radius:14px;padding:14px 16px;backdrop-filter:blur(10px);position:relative;overflow:hidden}
  .vscard .eng{font-family:Share Tech Mono;font-size:12px;letter-spacing:1.5px}
  .vscard .eq{font-family:Orbitron;font-weight:700;font-size:26px;margin:6px 0 2px}
  .card{background:var(--glass);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:20px;backdrop-filter:blur(10px)}
  .card h2{font-family:Orbitron;font-weight:600;font-size:13px;letter-spacing:2px;margin:0 0 14px;text-transform:uppercase}
  .engine{border-radius:16px;padding:18px;margin-bottom:22px;backdrop-filter:blur(10px);background:var(--glass);border:1px solid var(--line);border-left-width:4px}
  .engine .ehead{font-family:Orbitron;font-weight:700;font-size:16px;letter-spacing:2px;margin:0 0 4px}
  .engine .enote{font-family:Share Tech Mono;font-size:11px;color:var(--mut);margin-bottom:14px;letter-spacing:1px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(116px,1fr));gap:10px;margin-bottom:14px}
  .kpi{background:rgba(5,7,14,.35);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
  .kpi .l{color:var(--mut);font-family:Share Tech Mono;font-size:10px;letter-spacing:1px;text-transform:uppercase}
  .kpi .v{font-family:Orbitron;font-weight:700;font-size:17px;margin-top:5px}
  .sec{font-family:Share Tech Mono;font-size:11px;color:var(--mut);letter-spacing:1px;margin:14px 0 6px;text-transform:uppercase}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:right;padding:7px 10px;border-bottom:1px solid rgba(80,120,200,.12)}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--mut);font-family:Share Tech Mono;font-size:10px;letter-spacing:1px;font-weight:400;text-transform:uppercase}
  td{font-variant-numeric:tabular-nums} tbody tr:hover{background:rgba(34,211,238,.05)}
  .num{font-family:Share Tech Mono} .pos{color:var(--grn)} .neg{color:var(--red)}
  .bar{height:7px;border-radius:4px;background:linear-gradient(90deg,var(--cyan),var(--mag))}
  .pill{font-family:Share Tech Mono;padding:2px 9px;border-radius:999px;font-size:10px;letter-spacing:1px}
  .ok{background:rgba(52,211,153,.12);color:var(--grn);border:1px solid rgba(52,211,153,.4)}
  .err{background:rgba(251,113,133,.12);color:var(--red);border:1px solid rgba(251,113,133,.4)}
  .empty{color:var(--mut);padding:14px;text-align:center;font-family:Share Tech Mono;font-size:12px}
  .foot{color:var(--mut);font-family:Share Tech Mono;font-size:11px;letter-spacing:1px;margin-top:8px;text-align:center}
</style></head>
<body><div class="wrap">
  <h1>◢ SWING DUAL-ENGINE DECK ◣</h1>
  <div class="sub">MONTHLY vs WEEKLY vs NIFTY · PAPER CAPITAL · SYNC <span id="now"></span></div>
  <div id="regime"></div>
  __EMPTY__
  <div class="vs" id="vs"></div>
  <div class="card"><h2 style="color:#9fb3d1">▚ Head-to-Head Equity (vs Nifty 50)</h2><canvas id="chart" height="92"></canvas></div>
  <div id="engines"></div>
  <div class="foot">GENERATED LOCALLY FROM VM LEDGERS · NIFTY VIA YFINANCE · RE-RUN open_dashboard.bat TO SYNC</div>
</div>
<script>
const P = __PAYLOAD__;
document.getElementById("now").textContent = P.now;
const inr = n => "₹" + Math.round(n).toLocaleString("en-IN");
const sgn = (n,s="") => (n==null?'<span class="num">—</span>':`<span class="num ${n>=0?'pos':'neg'}">${n>=0?'▲':'▼'} ${n}${s}</span>`);

// Market regime card (Nifty level + 200-DMA bull/bear)
const RG = P.regime || {};
if(RG.regime){
  const bull = RG.regime==='BULLISH'; const col = bull?'#34d399':'#fb7185';
  const note = bull ? 'Risk-on backdrop — momentum tailwind.' : 'Risk-off backdrop — momentum headwind; expect deeper drawdowns.';
  document.getElementById("regime").innerHTML = `
   <div class="card" style="border-color:${col}55;border-left:4px solid ${col};display:flex;flex-wrap:wrap;gap:20px;align-items:center;margin-bottom:18px">
     <div style="font-family:Orbitron;font-weight:900;font-size:19px;letter-spacing:2px;color:${col}">${bull?'🟢':'🔴'} MARKET: ${RG.regime}</div>
     <div class="num" style="color:var(--mut)">NIFTY 50 · <b style="color:var(--ink);font-size:16px">${RG.level.toLocaleString('en-IN')}</b></div>
     <div class="num" style="color:var(--mut)">200-DMA ${RG.dma200.toLocaleString('en-IN')} · <span class="${RG.pct_vs_dma>=0?'pos':'neg'}">${RG.pct_vs_dma>=0?'+':''}${RG.pct_vs_dma}%</span></div>
     <div class="num" style="color:var(--mut)">1D <span class="${RG.day_change_pct>=0?'pos':'neg'}">${RG.day_change_pct>=0?'+':''}${RG.day_change_pct}%</span></div>
     <div class="num" style="color:var(--mut)">~1M <span class="${RG.mom20_pct>=0?'pos':'neg'}">${RG.mom20_pct>=0?'+':''}${RG.mom20_pct}%</span></div>
     <div style="flex:1;min-width:160px;color:var(--mut);font-size:13px">${note}</div>
   </div>`;
}

// VS strip
document.getElementById("vs").innerHTML = P.engines.map(e=>`
  <div class="vscard" style="border-color:${e.color}44">
    <div style="position:absolute;top:0;left:0;right:0;height:3px;background:${e.color}"></div>
    <div class="eng" style="color:${e.color}">${e.icon} ${e.label} <span style="color:var(--mut)">· ${e.note}</span></div>
    <div class="eq">${inr(e.m.equity)}</div>
    <div>${sgn(e.m.total_ret_pct,"%")} · <span style="color:var(--mut)">vs Nifty</span> ${sgn(e.m.alpha_vs_nifty,"%")} · DD <span class="neg num">${e.m.max_dd_pct}%</span> · ${e.m.n_holdings} held</div>
  </div>`).join("");

// Head-to-head chart (engines + Nifty benchmark)
const benc = P.benchmark || [];
const allDates = [...new Set([...P.engines.flatMap(e=>e.m.curve.map(p=>p.d)), ...benc.map(p=>p.d)])].sort();
const labels = allDates.length ? allDates : ["BASE","NOW"];
const datasets = P.engines.map(e=>{
  const map = Object.fromEntries(e.m.curve.map(p=>[p.d,p.e]));
  return {label:e.label, data:labels.map(d=> d in map ? map[d] : (e.m.curve.length?null:500000)),
    borderColor:e.color, backgroundColor:e.color+"22", fill:false, tension:.25, spanGaps:true,
    pointRadius:labels.length<40?2:0, borderWidth:2.2};
});
if(benc.length){
  const bmap = Object.fromEntries(benc.map(p=>[p.d,p.e]));
  datasets.push({label:"NIFTY 50", data:labels.map(d=> d in bmap ? bmap[d] : null),
    borderColor:"#94a3b8", borderDash:[6,4], backgroundColor:"transparent", fill:false,
    tension:.2, spanGaps:true, pointRadius:0, borderWidth:1.6});
}
new Chart(document.getElementById("chart"),{type:"line",data:{labels,datasets},
  options:{plugins:{legend:{labels:{color:"#9fb3d1",font:{family:'Share Tech Mono'}}}},
    scales:{x:{ticks:{color:"#6b7a99",maxTicksLimit:10,font:{family:'Share Tech Mono'}},grid:{color:"rgba(80,120,200,.10)"}},
            y:{ticks:{color:"#6b7a99",font:{family:'Share Tech Mono'},callback:v=>"₹"+(v/1000)+"k"},grid:{color:"rgba(80,120,200,.10)"}}}}});

// Per-engine detail blocks
const kpi=(l,v)=>`<div class="kpi"><div class="l">${l}</div><div class="v"><span class="num">${v}</span></div></div>`;
document.getElementById("engines").innerHTML = P.engines.map(e=>{
  const M=e.m;
  const pf = M.profit_factor==null?"—":M.profit_factor;
  const kpis = [
    ["Equity", inr(M.equity)], ["Return", `<span class="${M.total_ret_pct>=0?'pos':'neg'}">${M.total_ret_pct}%</span>`],
    ["vs Nifty", M.alpha_vs_nifty==null?"—":`<span class="${M.alpha_vs_nifty>=0?'pos':'neg'}">${M.alpha_vs_nifty}%</span>`],
    ["Cash", inr(M.cash)], ["Holdings", M.n_holdings], ["Max DD", `<span class="neg">${M.max_dd_pct}%</span>`],
    ["Win rate", M.win_rate==null?"—":M.win_rate+"%"], ["Profit factor", pf],
    ["Avg win", M.avg_win==null?"—":`<span class="pos">${M.avg_win}%</span>`],
    ["Avg loss", M.avg_loss==null?"—":`<span class="neg">${M.avg_loss}%</span>`],
    ["Best", M.best==null?"—":`<span class="pos">${M.best}%</span>`],
    ["Worst", M.worst==null?"—":`<span class="neg">${M.worst}%</span>`],
  ].map(([l,v])=>kpi(l,v)).join("");
  const H=M.holdings, T=M.trades, R=M.runs, S=M.sectors;
  const maxSec = S.length ? Math.max(...S.map(x=>x.value)) : 1;
  const sectors = S.length ? S.map(s=>`<tr><td>${s.sector}</td><td class="num">${s.n}</td><td class="num">${inr(s.value)}</td>
     <td style="width:30%"><div class="bar" style="width:${Math.max(6,Math.round(s.value/maxSec*100))}%"></div></td></tr>`).join("")
    : "";
  const sectorTbl = S.length ? `<table><tr><th>Sector</th><th>#</th><th>Value</th><th></th></tr>${sectors}</table>` : `<div class="empty">// no exposure yet</div>`;
  const holdings = H.length ? `<table><tr><th>Symbol</th><th>Since</th><th>Qty</th><th>Entry</th><th>Last</th><th>Value</th><th>Unreal.</th></tr>
    ${H.map(h=>`<tr><td>${h.symbol}</td><td class="num">${h.entry_date}</td><td class="num">${h.shares}</td><td class="num">${h.entry_price}</td><td class="num">${h.last_close}</td><td class="num">${inr(h.value)}</td><td>${sgn(h.upl_pct,"%")}</td></tr>`).join("")}</table>`
    : `<div class="empty">// no open positions</div>`;
  const trades = T.length ? `<table><tr><th>Symbol</th><th>Exit</th><th>Qty</th><th>P&L</th><th>Return</th></tr>
    ${T.map(t=>`<tr><td>${t.symbol}</td><td class="num">${t.exit_date}</td><td class="num">${t.shares}</td><td>${sgn(Math.round(t.pnl))}</td><td>${sgn(t.return_pct,"%")}</td></tr>`).join("")}</table>`
    : `<div class="empty">// no closed trades</div>`;
  const runs = R.length ? `<table><tr><th>Date</th><th>Status</th><th>Orders</th><th>Equity</th></tr>
    ${R.map(r=>`<tr><td class="num">${r.run_date}</td><td><span class="pill ${r.status==='error'?'err':'ok'}">${r.status.toUpperCase()}</span></td><td class="num">${r.n_orders}</td><td class="num">${r.equity!=null?inr(r.equity):"—"}</td></tr>`).join("")}</table>`
    : `<div class="empty">// no runs yet · first run Mon 8 Jun</div>`;
  return `<div class="engine" style="border-left-color:${e.color}">
    <div class="ehead" style="color:${e.color}">${e.icon} ${e.label}</div>
    <div class="enote">${e.note==='live'?'live · synced from VM':e.note}</div>
    <div class="grid">${kpis}</div>
    <div class="sec">Sector exposure</div>${sectorTbl}
    <div class="sec">Holdings</div>${holdings}
    <div class="sec">Closed trades</div>${trades}
    <div class="sec">Recent runs</div>${runs}
  </div>`;
}).join("");
</script>
</body></html>"""


def main() -> int:
    engines_data = []
    for e in ENGINES:
        local = HERE / f"_cache_{e['key']}.db"
        ok, note = refresh(e["remote"], local)
        L = read_ledger(local)
        if not L.get("exists"):
            L = {"exists": False, "equity": [], "positions": [], "trades": [],
                 "runs": [], "cash": INITIAL_CAPITAL}
        engines_data.append({**e, "note": note, "metrics": compute(L)})

    # Benchmark over the union span of both engines' equity dates.
    all_dates = sorted({d for e in engines_data for d in e["metrics"]["dates"]})
    benchmark: list = []
    if all_dates:
        benchmark = fetch_nifty(all_dates[0], all_dates[-1])
        for e in engines_data:
            e["metrics"]["alpha_vs_nifty"] = (
                None if e["metrics"]["total_ret_pct"] is None or
                nifty_return_over(benchmark, e["metrics"]["dates"]) is None
                else round(e["metrics"]["total_ret_pct"]
                           - nifty_return_over(benchmark, e["metrics"]["dates"]), 2))

    regime = fetch_market_regime()
    OUT_HTML.write_text(render(engines_data, benchmark, regime), encoding="utf-8")
    for e in engines_data:
        print(f"[dashboard] {e['key']}: {e['note']} | equity Rs "
              f"{e['metrics']['equity']:,.0f} | alpha {e['metrics']['alpha_vs_nifty']}")
    print(f"[dashboard] nifty points: {len(benchmark)} | "
          f"regime: {regime.get('regime', 'n/a')} (Nifty {regime.get('level', '?')}) "
          f"| wrote {OUT_HTML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
