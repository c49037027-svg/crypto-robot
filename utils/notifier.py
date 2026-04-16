"""
Telegram 通知模組
開倉、平倉、止損止盈、每日報告 → 推播到手機
"""
import requests
from datetime import datetime, timezone
from utils.logger import get_logger
from utils.helpers import format_usdt, format_pct

logger = get_logger("Notifier")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.enabled = bool(token and chat_id and
                            token != "your_telegram_bot_token")
        self.token   = token
        self.chat_id = chat_id
        self._base   = f"https://api.telegram.org/bot{token}/sendMessage"

        if self.enabled:
            logger.info("Telegram 通知已啟用")
        else:
            logger.info("Telegram 未設定，通知關閉")

    def send(self, text: str):
        if not self.enabled:
            return
        try:
            requests.post(self._base, json={
                "chat_id":    self.chat_id,
                "text":       text,
                "parse_mode": "HTML",
            }, timeout=5)
        except Exception as e:
            logger.warning(f"Telegram 發送失敗: {e}")

    def notify_start(self, symbols: list, balance: float, mode: str):
        self.send(
            f"🤖 <b>交易機器人啟動</b>\n"
            f"模式: {mode}\n"
            f"資金: {format_usdt(balance)}\n"
            f"交易對: {', '.join(symbols)}\n"
            f"時間: {datetime.now().strftime('%H:%M:%S')}"
        )

    def notify_open(self, symbol, side, entry, sl, tp, qty, rr, confidence):
        emoji = "📈" if side == "long" else "📉"
        self.send(
            f"{emoji} <b>開倉 {symbol}</b>\n"
            f"方向: {'做多 LONG' if side=='long' else '做空 SHORT'}\n"
            f"進場: <b>{entry:.4f}</b>\n"
            f"止損: {sl:.4f}  止盈: {tp:.4f}\n"
            f"盈虧比: 1:{rr:.2f}  數量: {qty:.4f}\n"
            f"AI信心: {confidence:.0f}%"
        )

    def notify_close(self, symbol, side, entry, exit_price, pnl, pnl_pct, reason):
        if reason == "tp":
            emoji, label = "✅", "止盈出場"
        elif reason == "sl":
            emoji, label = "❌", "止損出場"
        elif reason == "timeout":
            emoji, label = "⏰", "超時出場"
        else:
            emoji, label = "🔄", "手動出場"

        pnl_str = format_usdt(pnl)
        pct_str = format_pct(pnl_pct)
        self.send(
            f"{emoji} <b>{label} {symbol}</b>\n"
            f"進場: {entry:.4f} → 出場: {exit_price:.4f}\n"
            f"損益: <b>{pnl_str} ({pct_str})</b>"
        )

    def notify_daily_report(self, stats: dict):
        pnl = stats.get("total_pnl", 0)
        emoji = "📊"
        self.send(
            f"{emoji} <b>每日報告</b>\n"
            f"總損益: {format_usdt(pnl)} ({format_pct(stats.get('total_pnl_pct',0))})\n"
            f"勝率: {stats.get('win_rate',0):.1f}%  "
            f"交易數: {stats.get('closed_trades',0)}\n"
            f"最大回撤: {stats.get('max_drawdown',0):.2f}%\n"
            f"平均盈虧比: 1:{stats.get('avg_risk_reward',0):.2f}"
        )

    def notify_risk_halt(self, reason: str):
        self.send(f"⚠️ <b>風控暫停交易</b>\n原因: {reason}")
