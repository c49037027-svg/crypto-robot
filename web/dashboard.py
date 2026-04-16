"""
網頁監控儀表板
訪問 http://YOUR_IP:8080 查看即時狀態
"""
import asyncio
import threading
import json
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from utils.logger import get_logger

logger = get_logger("Dashboard")

# 全局狀態 (由 Orchestrator 更新)
_state = {
    "status":    "running",
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "cycle":      0,
    "equity":     0,
    "balance":    0,
    "total_pnl":  0,
    "total_pnl_pct": 0,
    "win_rate":   0,
    "closed_trades": 0,
    "max_drawdown": 0,
    "positions":  [],
    "recent_trades": [],
    "last_update": "",
}

HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>AI 量化交易機器人</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: -apple-system, monospace; padding: 20px; }
  h1 { color: #58a6ff; font-size: 1.4em; margin-bottom: 20px; }
  h2 { color: #8b949e; font-size: 0.9em; text-transform: uppercase; letter-spacing: 1px; margin: 20px 0 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card .label { color: #8b949e; font-size: 0.75em; margin-bottom: 6px; }
  .card .value { font-size: 1.3em; font-weight: bold; }
  .green { color: #3fb950; }
  .red   { color: #f85149; }
  .blue  { color: #58a6ff; }
  .yellow{ color: #d29922; }
  table { width: 100%; border-collapse: collapse; background: #161b22; border-radius: 8px; overflow: hidden; }
  th { background: #21262d; color: #8b949e; padding: 10px 14px; text-align: left; font-size: 0.8em; }
  td { padding: 10px 14px; border-top: 1px solid #21262d; font-size: 0.85em; }
  tr:hover td { background: #1c2128; }
  .badge { padding: 2px 8px; border-radius: 20px; font-size: 0.75em; font-weight: bold; }
  .badge-green { background: #1a4726; color: #3fb950; }
  .badge-red   { background: #4a1515; color: #f85149; }
  .badge-blue  { background: #1a2f6b; color: #58a6ff; }
  .status-bar { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                padding: 10px 16px; margin-bottom: 20px; display: flex;
                justify-content: space-between; align-items: center; font-size: 0.85em; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950;
         display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .empty { color: #8b949e; text-align: center; padding: 30px; }
</style>
</head>
<body>
<h1>⚡ AI 量化交易機器人</h1>

<div class="status-bar">
  <span><span class="dot"></span>運行中 | 啟動: {started_at} | 週期: #{cycle} | 更新: {last_update}</span>
  <span class="badge badge-blue">模擬交易</span>
</div>

<div class="grid">
  <div class="card">
    <div class="label">總資產</div>
    <div class="value blue">{equity} USDT</div>
  </div>
  <div class="card">
    <div class="label">可用資金</div>
    <div class="value">{balance} USDT</div>
  </div>
  <div class="card">
    <div class="label">總損益</div>
    <div class="value {pnl_color}">{total_pnl} USDT<br><small>{total_pnl_pct}</small></div>
  </div>
  <div class="card">
    <div class="label">勝率</div>
    <div class="value {wr_color}">{win_rate}%</div>
  </div>
  <div class="card">
    <div class="label">已完成交易</div>
    <div class="value">{closed_trades} 筆</div>
  </div>
  <div class="card">
    <div class="label">最大回撤</div>
    <div class="value {dd_color}">{max_drawdown}%</div>
  </div>
</div>

<h2>📋 當前持倉 ({pos_count})</h2>
{positions_html}

<h2>📜 最近交易</h2>
{trades_html}

<p style="color:#30363d;font-size:0.75em;margin-top:20px">每 30 秒自動刷新</p>
</body></html>"""


def _build_html():
    s = _state
    pnl = s["total_pnl"]
    pnl_pct = s["total_pnl_pct"]
    wr = s["win_rate"]
    dd = s["max_drawdown"]

    # 持倉表格
    if s["positions"]:
        rows = ""
        for p in s["positions"]:
            pnl_c = "green" if p["pnl"] >= 0 else "red"
            side_badge = "badge-green" if p["side"]=="long" else "badge-red"
            side_txt = "↑ LONG" if p["side"]=="long" else "↓ SHORT"
            rows += f"""<tr>
              <td><b>{p['symbol']}</b></td>
              <td><span class="badge {side_badge}">{side_txt}</span></td>
              <td>{p['entry']:.4f}</td>
              <td>{p['current']:.4f}</td>
              <td class="red">{p['sl']:.4f}</td>
              <td class="green">{p['tp']:.4f}</td>
              <td class="{pnl_c}">{p['pnl']:+.2f} ({p['pnl_pct']:+.2f}%)</td>
              <td>1:{p['rr']:.2f}</td>
              <td>{p['bars_held']}</td>
            </tr>"""
        positions_html = f"""<table>
          <tr><th>交易對</th><th>方向</th><th>進場價</th><th>當前價</th>
              <th>止損</th><th>止盈</th><th>損益</th><th>盈虧比</th><th>K線數</th></tr>
          {rows}</table>"""
    else:
        positions_html = '<div class="empty">目前無持倉</div>'

    # 最近交易
    recent = s["recent_trades"][-10:][::-1]
    if recent:
        rows = ""
        for t in recent:
            pnl_c = "green" if t["pnl"] >= 0 else "red"
            reason_map = {"tp":"止盈✅","sl":"止損❌","timeout":"超時⏰","manual":"手動"}
            reason = reason_map.get(t.get("exit_reason",""), t.get("exit_reason",""))
            rows += f"""<tr>
              <td>{t['symbol']}</td>
              <td>{'↑ LONG' if t['side']=='long' else '↓ SHORT'}</td>
              <td>{t.get('entry_price',0):.4f}</td>
              <td>{t.get('exit_price',0):.4f}</td>
              <td class="{pnl_c}">{t['pnl']:+.2f} ({t.get('pnl_pct',0):+.2f}%)</td>
              <td>{reason}</td>
              <td>{t.get('exit_time','')}</td>
            </tr>"""
        trades_html = f"""<table>
          <tr><th>交易對</th><th>方向</th><th>進場</th><th>出場</th>
              <th>損益</th><th>原因</th><th>時間</th></tr>
          {rows}</table>"""
    else:
        trades_html = '<div class="empty">尚無已完成交易</div>'

    return HTML.format(
        started_at   = s["started_at"],
        cycle        = s["cycle"],
        last_update  = s["last_update"],
        equity       = f"{s['equity']:,.2f}",
        balance      = f"{s['balance']:,.2f}",
        total_pnl    = f"{pnl:+,.2f}",
        total_pnl_pct= f"{pnl_pct:+.2f}%",
        pnl_color    = "green" if pnl >= 0 else "red",
        win_rate     = f"{wr:.1f}",
        wr_color     = "green" if wr >= 50 else "red",
        closed_trades= s["closed_trades"],
        max_drawdown = f"{dd:.2f}",
        dd_color     = "green" if dd < 5 else "red",
        pos_count    = len(s["positions"]),
        positions_html = positions_html,
        trades_html  = trades_html,
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api":
            body = json.dumps(_state, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            body = _build_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass  # 不輸出 HTTP 日誌


def update_state(**kwargs):
    """由 Orchestrator 呼叫，更新儀表板狀態"""
    _state.update(kwargs)
    _state["last_update"] = datetime.now().strftime("%H:%M:%S")


def start_dashboard(port: int = 8080):
    """在背景執行緒啟動 HTTP 伺服器"""
    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"儀表板已啟動 → http://0.0.0.0:{port}")
    return server
