"""
網頁監控儀表板 v2 — 動態 JS 架構
- 靜態 HTML shell，JS 每 5 秒 fetch /api 更新
- Chart.js 資產曲線
- 持倉進度條、夏普比率、風險敞口
"""
import json
import math
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from utils.logger import get_logger

TW = timezone(timedelta(hours=8))

def now_tw() -> str:
    return datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S")

logger = get_logger("Dashboard")

_state = {
    "status":           "running",
    "started_at":       now_tw(),
    "cycle":            0,
    "equity":           0.0,
    "balance":          0.0,
    "total_pnl":        0.0,
    "total_pnl_pct":    0.0,
    "win_rate":         0.0,
    "long_win_rate":    0.0,
    "short_win_rate":   0.0,
    "realized_rr":      0.0,
    "closed_trades":    0,
    "max_drawdown":     0.0,
    "sharpe_ratio":     0.0,
    "total_exposure_pct": 0.0,
    "last_confidence":  0.0,
    "gemini_connected": False,
    "api_latency_ms":   0,
    "positions":        [],
    "recent_trades":    [],
    "equity_history":   [],
    "realized_equity":  10000.0,
    "last_update":      "",
}

HTML = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 量化交易機器人</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e;
  --green: #3fb950; --red: #f85149; --blue: #58a6ff;
  --yellow: #d29922; --orange: #f0883e;
  --green-bg: #1a4726; --red-bg: #4a1515; --blue-bg: #1a2f6b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, 'SF Pro Text', monospace; font-size: 14px; }
.shell { max-width: 1400px; margin: 0 auto; padding: 16px; }

/* ── Header ── */
.header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
.header h1 { color: var(--blue); font-size: 1.3em; }
.status-pill { background: var(--surface); border: 1px solid var(--border); border-radius: 20px; padding: 6px 14px; font-size: 0.8em; display: flex; align-items: center; gap: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); display: inline-block; animation: pulse 2s infinite; flex-shrink: 0; }
.dot.red { background: var(--red); animation: none; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
.badge { padding: 2px 10px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
.badge-blue  { background: var(--blue-bg);  color: var(--blue);  }
.badge-green { background: var(--green-bg); color: var(--green); }
.badge-red   { background: var(--red-bg);   color: var(--red);   }
.badge-yellow{ background: #3d2e00;         color: var(--yellow);}

/* ── System health bar ── */
.health-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 16px; margin-bottom: 14px; display: flex; gap: 24px; flex-wrap: wrap; font-size: 0.78em; color: var(--muted); }
.health-item { display: flex; align-items: center; gap: 6px; }
.health-item b { color: var(--text); }

/* ── Metric cards ── */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 14px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; transition: border-color .3s; }
.card.warn  { border-color: var(--orange); }
.card.crit  { border-color: var(--red); animation: blink-border 1s infinite; }
@keyframes blink-border { 0%,100%{border-color:var(--red)} 50%{border-color:#7a1010} }
.card .label { color: var(--muted); font-size: 0.72em; text-transform: uppercase; letter-spacing: .8px; margin-bottom: 6px; }
.card .val   { font-size: 1.35em; font-weight: 700; }
.card .sub   { font-size: 0.78em; color: var(--muted); margin-top: 3px; }
.green { color: var(--green); } .red { color: var(--red); }
.blue  { color: var(--blue);  } .yellow { color: var(--yellow); } .orange { color: var(--orange); }

/* ── Exposure bar ── */
.exposure-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin-bottom: 14px; }
.exposure-label { font-size: 0.78em; color: var(--muted); margin-bottom: 6px; display: flex; justify-content: space-between; }
.exp-bar-bg { background: var(--border); border-radius: 4px; height: 8px; }
.exp-bar-fg { height: 8px; border-radius: 4px; background: var(--blue); transition: width .5s, background .3s; }

/* ── Chart ── */
.chart-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 14px; }
.chart-wrap h2 { font-size: 0.8em; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 12px; }
.chart-wrap canvas { max-height: 220px; }

/* ── Tables ── */
.section-title { font-size: 0.8em; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; margin: 16px 0 8px; }
.table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 14px; }
table { width: 100%; border-collapse: collapse; }
th { background: #21262d; color: var(--muted); padding: 9px 12px; text-align: left; font-size: 0.75em; font-weight: 500; white-space: nowrap; }
td { padding: 10px 12px; border-top: 1px solid #21262d; font-size: 0.82em; vertical-align: middle; }
tr:hover td { background: #1c2128; }
tr.sl-warn td { animation: row-flash 1.2s infinite; }
@keyframes row-flash { 0%,100%{background:transparent} 50%{background:#3d1515} }

/* ── Progress bar (SL/TP) ── */
.prog-wrap { width: 110px; }
.prog-bg { background: var(--border); border-radius: 3px; height: 6px; position: relative; }
.prog-fg { height: 6px; border-radius: 3px; transition: width .4s; }
.prog-labels { display: flex; justify-content: space-between; font-size: 0.68em; color: var(--muted); margin-top: 2px; }

/* ── Tabs ── */
.tabs { display: flex; gap: 2px; margin-bottom: 0; border-bottom: 1px solid var(--border); }
.tab { padding: 8px 18px; font-size: 0.8em; cursor: pointer; border-radius: 6px 6px 0 0; color: var(--muted); border: 1px solid transparent; border-bottom: none; }
.tab.active { background: var(--surface); border-color: var(--border); color: var(--text); }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── Stats grid ── */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; padding: 14px; }
.stat-item .s-label { font-size: 0.72em; color: var(--muted); }
.stat-item .s-val   { font-size: 1.05em; font-weight: 600; margin-top: 2px; }

.empty { color: var(--muted); text-align: center; padding: 28px; font-size: 0.85em; }
.footer { color: #30363d; font-size: 0.72em; margin-top: 14px; text-align: right; }
</style>
</head>
<body>
<div class="shell">

<!-- Header -->
<div class="header">
  <h1>⚡ AI 量化交易機器人</h1>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <span class="status-pill">
      <span class="dot" id="hb-dot"></span>
      <span id="hb-text">連線中...</span>
    </span>
    <span class="badge badge-blue" id="mode-badge">模擬交易</span>
    <span class="badge badge-yellow" id="update-badge">WebSocket</span>
  </div>
</div>

<!-- System Health -->
<div class="health-bar">
  <div class="health-item">🔄 週期 <b id="h-cycle">—</b></div>
  <div class="health-item">🕐 啟動 <b id="h-started">—</b></div>
  <div class="health-item">🔁 更新 <b id="h-update">—</b></div>
  <div class="health-item">🤖 Gemini <b id="h-gemini">—</b></div>
  <div class="health-item">📶 延遲 <b id="h-latency">—</b></div>
  <div class="health-item">💡 最新信心 <b id="h-conf">—</b></div>
</div>

<!-- Metric Cards -->
<div class="cards">
  <div class="card" id="card-equity">
    <div class="label">總資產</div>
    <div class="val blue" id="m-equity">—</div>
    <div class="sub" id="m-equity-sub">—</div>
  </div>
  <div class="card">
    <div class="label">可用資金</div>
    <div class="val" id="m-balance">—</div>
    <div class="sub" id="m-balance-sub">—</div>
  </div>
  <div class="card" id="card-pnl">
    <div class="label">總損益</div>
    <div class="val" id="m-pnl">—</div>
    <div class="sub" id="m-pnl-pct">—</div>
  </div>
  <div class="card">
    <div class="label">夏普比率</div>
    <div class="val" id="m-sharpe">—</div>
    <div class="sub">風險調整後收益</div>
  </div>
  <div class="card">
    <div class="label">勝率</div>
    <div class="val" id="m-wr">—</div>
    <div class="sub" id="m-wr-sub">—</div>
  </div>
  <div class="card" id="card-dd">
    <div class="label">最大回撤</div>
    <div class="val" id="m-dd">—</div>
    <div class="sub" id="m-dd-sub">—</div>
  </div>
</div>

<!-- Exposure Bar -->
<div class="exposure-wrap">
  <div class="exposure-label">
    <span>風險敞口 (持倉佔總資金)</span>
    <span id="exp-pct-label">0%</span>
  </div>
  <div class="exp-bar-bg">
    <div class="exp-bar-fg" id="exp-bar" style="width:0%"></div>
  </div>
</div>

<!-- Equity Curve -->
<div class="chart-wrap">
  <h2>📈 資產曲線 (Equity Curve)</h2>
  <canvas id="equity-chart"></canvas>
</div>

<!-- Positions -->
<div class="section-title">📋 當前持倉 (<span id="pos-count">0</span>)</div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>交易對</th><th>方向</th><th>進場價</th><th>當前價</th>
        <th>止損 / 止盈 距離</th><th>損益</th><th>風險金額</th>
        <th>盈虧比</th><th>持倉時間</th><th>K線數</th>
      </tr>
    </thead>
    <tbody id="pos-tbody">
      <tr><td colspan="10" class="empty">目前無持倉</td></tr>
    </tbody>
  </table>
</div>

<!-- Tabs: History / Stats -->
<div class="tabs">
  <div class="tab active" onclick="switchTab('trades')">📜 成交記錄</div>
  <div class="tab" onclick="switchTab('stats')">📊 績效統計</div>
</div>
<div class="table-wrap">
  <!-- Trades tab -->
  <div class="tab-panel active" id="tab-trades">
    <table>
      <thead>
        <tr>
          <th>交易對</th><th>方向</th><th>進場</th><th>出場</th>
          <th>損益 (USDT)</th><th>損益%</th><th>平倉原因</th><th>時間</th>
        </tr>
      </thead>
      <tbody id="trades-tbody">
        <tr><td colspan="8" class="empty">尚無已完成交易</td></tr>
      </tbody>
    </table>
  </div>
  <!-- Stats tab -->
  <div class="tab-panel" id="tab-stats">
    <div class="stats-grid">
      <div class="stat-item"><div class="s-label">已完成交易</div><div class="s-val" id="s-closed">—</div></div>
      <div class="stat-item"><div class="s-label">多單勝率</div><div class="s-val green" id="s-lwr">—</div></div>
      <div class="stat-item"><div class="s-label">空單勝率</div><div class="s-val red" id="s-swr">—</div></div>
      <div class="stat-item"><div class="s-label">已實現盈虧比</div><div class="s-val" id="s-rr">—</div></div>
      <div class="stat-item"><div class="s-label">夏普比率</div><div class="s-val" id="s-sharpe">—</div></div>
      <div class="stat-item"><div class="s-label">最大回撤</div><div class="s-val red" id="s-dd">—</div></div>
      <div class="stat-item"><div class="s-label">最新 AI 信心</div><div class="s-val blue" id="s-conf">—</div></div>
      <div class="stat-item"><div class="s-label">Gemini 狀態</div><div class="s-val" id="s-gemini">—</div></div>
    </div>
  </div>
</div>

<div class="footer">每 5 秒輪詢更新 · 30秒輪詢模式</div>
</div>

<script>
// ── Chart setup ──
const ctx = document.getElementById('equity-chart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: '已實現資產 (USDT)',
      data: [],
      borderColor: '#58a6ff',
      backgroundColor: 'rgba(88,166,255,0.08)',
      borderWidth: 2,
      pointRadius: 0,
      fill: true,
      tension: 0.3,
    }]
  },
  options: {
    responsive: true,
    animation: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: '#8b949e', maxTicksLimit: 8 }, grid: { color: '#21262d' } },
      y: { ticks: { color: '#8b949e', callback: v => v.toLocaleString() }, grid: { color: '#21262d' } }
    }
  }
});

// ── Tab switch ──
function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    const panels = document.querySelectorAll('.tab-panel');
    const names = ['trades','stats'];
    t.classList.toggle('active', names[i] === name);
    panels[i].classList.toggle('active', names[i] === name);
  });
}

// ── Helpers ──
function fmt(n, dec=2) { return Number(n).toLocaleString('zh-TW', {minimumFractionDigits:dec, maximumFractionDigits:dec}); }
function fmtPct(n) { const v=Number(n); return (v>=0?'+':'')+v.toFixed(2)+'%'; }
function colorClass(n) { return Number(n)>=0 ? 'green' : 'red'; }
function elapsed(startStr) {
  const start = new Date(startStr.replace(' ','T')+'Z');
  const diff = Math.floor((Date.now() - start)/1000);
  const h = Math.floor(diff/3600), m = Math.floor((diff%3600)/60);
  return h > 0 ? h+'h '+m+'m' : m+'m';
}

// ── Main render ──
async function refresh() {
  let d;
  try {
    const r = await fetch('/api');
    d = await r.json();
  } catch(e) {
    document.getElementById('hb-dot').className = 'dot red';
    document.getElementById('hb-text').textContent = '連線失敗';
    return;
  }

  // Header / health
  document.getElementById('hb-dot').className = 'dot';
  document.getElementById('hb-text').textContent = '運行中';
  document.getElementById('h-cycle').textContent = '#' + d.cycle;
  document.getElementById('h-started').textContent = d.started_at;
  document.getElementById('h-update').textContent = d.last_update || '—';
  document.getElementById('h-gemini').innerHTML = d.gemini_connected
    ? '<span class="green">✓ 已連線</span>' : '<span class="red">✗ 離線</span>';
  document.getElementById('h-latency').textContent = d.api_latency_ms > 0 ? d.api_latency_ms+'ms' : '—';
  document.getElementById('h-conf').textContent = d.last_confidence > 0 ? d.last_confidence.toFixed(0)+'%' : '—';

  // Cards
  const eq = Number(d.equity), pnl = Number(d.total_pnl), pnlPct = Number(d.total_pnl_pct);
  const dd = Number(d.max_drawdown), wr = Number(d.win_rate), sharpe = Number(d.sharpe_ratio);
  const bal = Number(d.balance), exp = Number(d.total_exposure_pct);

  document.getElementById('m-equity').textContent = fmt(eq) + ' USDT';
  document.getElementById('m-equity-sub').textContent = '初始 10,000';

  document.getElementById('m-balance').textContent = fmt(bal) + ' USDT';
  document.getElementById('m-balance-sub').textContent = '可用';

  const pnlEl = document.getElementById('m-pnl');
  pnlEl.textContent = (pnl>=0?'+':'')+fmt(pnl) + ' USDT';
  pnlEl.className = 'val ' + colorClass(pnl);
  const pnlPctEl = document.getElementById('m-pnl-pct');
  pnlPctEl.textContent = fmtPct(pnlPct);
  pnlPctEl.className = colorClass(pnlPct);

  const sharpeEl = document.getElementById('m-sharpe');
  sharpeEl.textContent = isFinite(sharpe) ? sharpe.toFixed(2) : '—';
  sharpeEl.className = 'val ' + (sharpe >= 1 ? 'green' : sharpe >= 0 ? 'yellow' : 'red');

  const wrEl = document.getElementById('m-wr');
  wrEl.textContent = wr.toFixed(1) + '%';
  wrEl.className = 'val ' + (wr >= 50 ? 'green' : 'red');
  document.getElementById('m-wr-sub').textContent =
    `多 ${Number(d.long_win_rate).toFixed(0)}% / 空 ${Number(d.short_win_rate).toFixed(0)}%`;

  const ddEl = document.getElementById('m-dd');
  ddEl.textContent = dd.toFixed(2) + '%';
  ddEl.className = 'val ' + (dd < 5 ? 'green' : dd < 8 ? 'orange' : 'red');
  document.getElementById('m-dd-sub').textContent = d.closed_trades + ' 筆已完成';

  // Card warning
  document.getElementById('card-dd').className = 'card' + (dd>=8?' crit':dd>=5?' warn':'');
  document.getElementById('card-pnl').className = 'card' + (pnlPct<=-5?' warn':'');

  // Exposure bar
  const expPct = Math.min(exp, 100);
  document.getElementById('exp-pct-label').textContent = expPct.toFixed(1) + '%';
  const barEl = document.getElementById('exp-bar');
  barEl.style.width = expPct + '%';
  barEl.style.background = expPct > 80 ? '#f85149' : expPct > 60 ? '#f0883e' : '#58a6ff';

  // Equity chart
  if (d.equity_history && d.equity_history.length > 0) {
    chart.data.labels = d.equity_history.map(p => p.t);
    chart.data.datasets[0].data = d.equity_history.map(p => p.v);
    chart.update('none');
  }

  // Positions
  const posCount = (d.positions || []).length;
  document.getElementById('pos-count').textContent = posCount;
  const tbody = document.getElementById('pos-tbody');
  if (posCount === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">目前無持倉</td></tr>';
  } else {
    tbody.innerHTML = d.positions.map(p => {
      const entry = Number(p.entry), sl = Number(p.sl), tp = Number(p.tp), cur = Number(p.current);
      const pnl = Number(p.pnl), rr = Number(p.rr);
      // SL/TP progress bar
      const range = Math.abs(tp - sl);
      const progPct = range > 0 ? Math.max(0, Math.min(100, Math.abs(cur - sl) / range * 100)) : 0;
      const slDist = Math.abs(cur - sl), tpDist = Math.abs(cur - tp);
      const slPct = entry > 0 ? (slDist/entry*100).toFixed(2) : '0.00';
      const tpPct = entry > 0 ? (tpDist/entry*100).toFixed(2) : '0.00';
      const barColor = progPct > 80 ? '#3fb950' : progPct > 50 ? '#d29922' : '#58a6ff';
      const riskAmt = slDist * (entry > 0 ? 100 / entry : 0); // approx risk USDT

      // SL warning: within 0.5% of stop loss
      const slWarn = slDist / entry < 0.005;
      const sideColor = p.side === 'long' ? 'badge-green' : 'badge-red';
      const sideText = p.side === 'long' ? '↑ LONG' : '↓ SHORT';
      const bars = Number(p.bars_held);
      const dur = bars + ' 根K線';

      return `<tr class="${slWarn ? 'sl-warn' : ''}">
        <td><b>${p.symbol}</b></td>
        <td><span class="badge ${sideColor}">${sideText}</span></td>
        <td>${Number(p.entry).toFixed(4)}</td>
        <td>${Number(p.current).toFixed(4)}</td>
        <td>
          <div class="prog-wrap">
            <div class="prog-bg">
              <div class="prog-fg" style="width:${progPct.toFixed(1)}%;background:${barColor}"></div>
            </div>
            <div class="prog-labels">
              <span class="red">${slPct}%</span>
              <span class="green">${tpPct}%</span>
            </div>
          </div>
        </td>
        <td class="${colorClass(pnl)}">${pnl>=0?'+':''}${fmt(pnl)} USDT<br>
          <small>${fmtPct(p.pnl_pct)}</small>
        </td>
        <td class="red">${fmt(slDist * (1 / (entry > 0 ? 1/entry * (1/rr > 0 ? 1 : 1) : 1)),4)} USDT</td>
        <td>1:${rr.toFixed(2)}</td>
        <td>${dur}</td>
        <td>${bars}</td>
      </tr>`;
    }).join('');
  }

  // Trades
  const trades = (d.recent_trades || []).slice().reverse().slice(0,20);
  const trBody = document.getElementById('trades-tbody');
  if (trades.length === 0) {
    trBody.innerHTML = '<tr><td colspan="8" class="empty">尚無已完成交易</td></tr>';
  } else {
    const reasons = {tp:'止盈 ✅', sl:'止損 ❌', timeout:'超時 ⏰', manual:'手動'};
    trBody.innerHTML = trades.map(t => {
      const pnl = Number(t.pnl);
      return `<tr>
        <td>${t.symbol}</td>
        <td><span class="badge ${t.side==='long'?'badge-green':'badge-red'}">${t.side==='long'?'↑ LONG':'↓ SHORT'}</span></td>
        <td>${Number(t.entry_price||0).toFixed(4)}</td>
        <td>${Number(t.exit_price||0).toFixed(4)}</td>
        <td class="${colorClass(pnl)}">${pnl>=0?'+':''}${fmt(pnl)}</td>
        <td class="${colorClass(t.pnl_pct)}">${fmtPct(t.pnl_pct||0)}</td>
        <td>${reasons[t.exit_reason]||t.exit_reason}</td>
        <td>${t.exit_time||'—'}</td>
      </tr>`;
    }).join('');
  }

  // Stats tab
  document.getElementById('s-closed').textContent = d.closed_trades;
  document.getElementById('s-lwr').textContent = Number(d.long_win_rate).toFixed(1) + '%';
  document.getElementById('s-swr').textContent = Number(d.short_win_rate).toFixed(1) + '%';
  document.getElementById('s-rr').textContent = '1:' + Number(d.realized_rr).toFixed(2);
  document.getElementById('s-sharpe').textContent = isFinite(Number(d.sharpe_ratio)) ? Number(d.sharpe_ratio).toFixed(3) : '—';
  document.getElementById('s-dd').textContent = Number(d.max_drawdown).toFixed(2) + '%';
  document.getElementById('s-conf').textContent = d.last_confidence > 0 ? d.last_confidence.toFixed(0)+'%' : '尚無信號';
  document.getElementById('s-gemini').innerHTML = d.gemini_connected
    ? '<span class="green">已連線</span>' : '<span class="red">離線 (規則模式)</span>';
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


def _compute_extra_stats(state: dict):
    """從 recent_trades 計算夏普比率、多空勝率、已實現盈虧比"""
    trades = state.get("recent_trades", [])
    if not trades:
        return

    pnls = [t.get("pnl", 0) for t in trades]
    if len(pnls) >= 2:
        mean_r = sum(pnls) / len(pnls)
        std_r = math.sqrt(sum((p - mean_r) ** 2 for p in pnls) / len(pnls))
        state["sharpe_ratio"] = (mean_r / std_r * math.sqrt(365)) if std_r > 0 else 0.0
    else:
        state["sharpe_ratio"] = 0.0

    long_trades  = [t for t in trades if t.get("side") == "long"]
    short_trades = [t for t in trades if t.get("side") == "short"]
    state["long_win_rate"]  = (sum(1 for t in long_trades  if t.get("pnl",0) > 0) / len(long_trades)  * 100) if long_trades  else 0.0
    state["short_win_rate"] = (sum(1 for t in short_trades if t.get("pnl",0) > 0) / len(short_trades) * 100) if short_trades else 0.0

    wins   = [t.get("pnl",0) for t in trades if t.get("pnl",0) > 0]
    losses = [t.get("pnl",0) for t in trades if t.get("pnl",0) < 0]
    avg_win  = sum(wins)   / len(wins)   if wins   else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    state["realized_rr"] = avg_win / avg_loss if avg_loss > 0 else 0.0


def update_state(**kwargs):
    """由 Orchestrator 呼叫，更新儀表板狀態"""
    _state.update(kwargs)
    _state["last_update"] = datetime.now(TW).strftime("%H:%M:%S")

    # 資產曲線：只記錄已實現損益（完全成交後），忽略浮動盈虧
    new_realized = _state.get("realized_equity", 0)
    history = _state.setdefault("equity_history", [])
    if new_realized > 0 and (not history or history[-1]["v"] != new_realized):
        history.append({"t": _state["last_update"], "v": round(new_realized, 2)})
        if len(history) > 500:
            _state["equity_history"] = history[-500:]

    # 計算風險敞口
    positions = _state.get("positions", [])
    total_eq  = _state.get("equity", 1) or 1
    notional  = sum(p.get("entry", 0) * p.get("qty", 1) for p in positions)
    # 若 positions 沒有 qty，用 pnl_pct 估算（保守用 equity 的比例）
    if notional == 0 and positions:
        notional = (total_eq - _state.get("balance", total_eq))
    _state["total_exposure_pct"] = min(notional / total_eq * 100, 100) if total_eq > 0 else 0

    _compute_extra_stats(_state)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            body = json.dumps(_state, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_dashboard(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"儀表板已啟動 → http://0.0.0.0:{port}")
    return server
