"""
持倉監控代理人 (PositionMonitorAgent)
職責: 即時監控所有持倉，執行止損/止盈，更新移動止損
"""
from typing import Dict, List, Optional
from core.portfolio import PaperPortfolio, Position, Trade
from core.data_manager import DataManager
from agents.risk_manager import RiskManagerAgent
from utils.logger import get_logger
from utils.helpers import format_usdt, format_pct

logger = get_logger("PositionMonitor")


class PositionMonitorAgent:
    """
    持倉監控代理人
    每個交易週期執行:
    1. 檢查所有持倉是否觸及 SL/TP
    2. 更新移動止損
    3. 檢查持倉時間是否超限
    4. 計算並報告未實現損益
    """

    def __init__(self, portfolio: PaperPortfolio, risk_manager: RiskManagerAgent,
                 data_manager: DataManager, trailing_stop: bool = True,
                 max_position_age_bars: int = 48):
        self.portfolio = portfolio
        self.risk_mgr = risk_manager
        self.data_mgr = data_manager
        self.trailing_stop = trailing_stop
        self.max_age_bars = max_position_age_bars

    async def monitor_all(self) -> List[Trade]:
        """監控所有持倉，返回本輪已平倉（含分批）的交易列表"""
        closed_trades = []
        symbols_to_close = []

        for symbol, pos in list(self.portfolio.positions.items()):
            current_price = self.data_mgr.get_current_price(symbol)
            if current_price <= 0:
                logger.warning(f"無法取得 {symbol} 當前價格，跳過")
                continue

            atr = self.data_mgr.get_atr(symbol)
            pos.bars_held += 1

            # ── TP1 分批平倉（還沒觸發過才檢查）──
            if not pos.tp1_hit and pos.take_profit_1 > 0:
                tp1_triggered = (
                    (pos.side == "long"  and current_price >= pos.take_profit_1) or
                    (pos.side == "short" and current_price <= pos.take_profit_1)
                )
                if tp1_triggered:
                    qty_close = pos.initial_quantity * self.risk_mgr.cfg.tp1_close_pct
                    trade = self.portfolio.partial_close_position(symbol, qty_close, current_price, "tp1")
                    if trade:
                        closed_trades.append(trade)
                    # 移止損到成本價（保本）
                    pos.stop_loss = pos.entry_price
                    pos.trailing_sl = 0.0
                    pos.tp1_hit = True
                    logger.info(f"[TP1] {symbol} 分批平倉 {self.risk_mgr.cfg.tp1_close_pct*100:.0f}% | 止損移至成本 {pos.entry_price:.4f}")

            # ── 移動止損啟動門檻檢查 ──
            if not pos.trailing_active and atr > 0:
                price_move = abs(current_price - pos.entry_price)
                if price_move >= self.risk_mgr.cfg.trailing_activate_atr * atr:
                    pos.trailing_active = True
                    logger.info(f"[移動止損啟動] {symbol} 浮盈已達 {self.risk_mgr.cfg.trailing_activate_atr}x ATR")

            # ── 移動止損更新（啟動後才追蹤）──
            if self.trailing_stop and pos.trailing_active and atr > 0:
                self.portfolio.update_trailing_stop(
                    symbol, current_price, atr,
                    self.risk_mgr.cfg.trailing_stop_atr_mult
                )

            # ── 全倉平倉檢查 ──
            exit_reason = self._check_exit(pos, current_price)
            if exit_reason:
                symbols_to_close.append((symbol, current_price, exit_reason))
            else:
                self._log_position_status(pos, current_price, atr)

        # 執行全倉平倉
        for symbol, price, reason in symbols_to_close:
            trade = self.portfolio.close_position(symbol, price, reason)
            if trade:
                closed_trades.append(trade)

        return closed_trades

    def _check_exit(self, pos: Position, current_price: float) -> Optional[str]:
        """檢查是否需要平倉，返回平倉原因或 None"""
        # 止損觸發
        if pos.is_sl_hit(current_price):
            sl = pos.trailing_sl if pos.trailing_sl > 0 else pos.stop_loss
            logger.warning(
                f"[止損] {pos.symbol} 觸及止損 | "
                f"當前價: {current_price:.4f} | SL: {sl:.4f}"
            )
            return "sl"

        # 止盈觸發
        if pos.is_tp_hit(current_price):
            logger.info(
                f"[止盈] {pos.symbol} 觸及止盈 🎯 | "
                f"當前價: {current_price:.4f} | TP: {pos.take_profit:.4f}"
            )
            return "tp"

        # 持倉超時
        if pos.bars_held >= self.max_age_bars:
            logger.warning(
                f"[超時] {pos.symbol} 持倉已達 {pos.bars_held} 根 K 線，強制平倉"
            )
            return "timeout"

        return None

    def _log_position_status(self, pos: Position, current_price: float, atr: float):
        """記錄當前持倉狀態"""
        pnl = pos.unrealized_pnl(current_price)
        pnl_pct = pos.unrealized_pct(current_price)
        sl = pos.trailing_sl if pos.trailing_sl > 0 else pos.stop_loss

        # 計算距止損/止盈的距離百分比
        if pos.side == "long":
            dist_to_sl = (current_price - sl) / current_price * 100
            dist_to_tp = (pos.take_profit - current_price) / current_price * 100
        else:
            dist_to_sl = (sl - current_price) / current_price * 100
            dist_to_tp = (current_price - pos.take_profit) / current_price * 100

        emoji = "📈" if pnl >= 0 else "📉"
        logger.debug(
            f"{emoji} {pos.symbol} {pos.side.upper()} | "
            f"價格: {current_price:.4f} | "
            f"損益: {format_usdt(pnl)} ({format_pct(pnl_pct)}) | "
            f"距SL: {dist_to_sl:.2f}% | 距TP: {dist_to_tp:.2f}% | "
            f"已持 {pos.bars_held} 根"
        )

    def get_portfolio_status(self) -> Dict:
        """返回所有持倉的即時狀態摘要"""
        positions_info = []
        prices = {}

        for symbol, pos in self.portfolio.positions.items():
            price = self.data_mgr.get_current_price(symbol)
            if price > 0:
                prices[symbol] = price
                positions_info.append({
                    "symbol": symbol,
                    "side": pos.side,
                    "entry": pos.entry_price,
                    "current": price,
                    "sl": pos.trailing_sl if pos.trailing_sl > 0 else pos.stop_loss,
                    "tp": pos.take_profit,
                    "rr": pos.risk_reward,
                    "pnl": pos.unrealized_pnl(price),
                    "pnl_pct": pos.unrealized_pct(price),
                    "bars_held": pos.bars_held,
                })

        total_equity = self.portfolio.total_equity(prices)
        stats = self.portfolio.stats_summary()

        return {
            "total_equity": total_equity,
            "available_balance": self.portfolio.balance,
            "positions": positions_info,
            "stats": stats,
        }
