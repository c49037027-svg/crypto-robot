"""
交易執行代理人 (ExecutionAgent)
職責: 接收風控通過的交易設定，執行開倉、設定 SL/TP
"""
import asyncio
from typing import Dict, Optional
from core.exchange_client import ExchangeClient
from core.portfolio import PaperPortfolio, Position
from utils.logger import get_logger
from utils.helpers import format_usdt

logger = get_logger("ExecutionAgent")


class ExecutionAgent:
    """
    執行代理人
    - 模擬模式: 直接通過 PaperPortfolio 記錄
    - 真實模式: 通過 ccxt 在交易所下單
    """

    def __init__(self, exchange: ExchangeClient, portfolio: PaperPortfolio):
        self.exchange = exchange
        self.portfolio = portfolio

    async def execute_trade(self, trade_setup: Dict, confidence: float) -> bool:
        """
        執行交易
        trade_setup 來自 RiskManagerAgent.calculate_trade_setup()
        """
        symbol    = trade_setup["symbol"]
        signal    = trade_setup["signal"]
        entry     = trade_setup["entry_price"]
        sl        = trade_setup["stop_loss"]
        tp1       = trade_setup.get("take_profit_1", 0.0)
        tp        = trade_setup["take_profit"]
        qty       = trade_setup["quantity"]
        rr        = trade_setup["risk_reward"]
        risk_amt  = trade_setup["risk_amount"]
        notional  = trade_setup["notional"]

        side = "buy" if signal == "LONG" else "sell"

        logger.info(
            f"\n{'='*60}\n"
            f"  執行 {signal} 交易: {symbol}\n"
            f"  進場價: {entry:.4f} USDT\n"
            f"  止損:   {sl:.4f} USDT  (風險: {format_usdt(risk_amt)})\n"
            f"  止盈:   {tp:.4f} USDT  (預期利潤: {format_usdt(risk_amt * rr)})\n"
            f"  盈虧比: 1:{rr:.2f}\n"
            f"  數量:   {qty:.6f}  (名義: {format_usdt(notional)})\n"
            f"  信心:   {confidence:.0f}%\n"
            f"{'='*60}"
        )

        # 下主單
        order = await self.exchange.place_order(
            symbol=symbol,
            side=side,
            amount=qty,
            price=entry,
            order_type="market",
        )

        if not order:
            logger.error(f"下單失敗: {symbol}")
            return False

        # 記錄到模擬帳戶
        position = Position(
            symbol=symbol,
            side="long" if signal == "LONG" else "short",
            entry_price=entry,
            quantity=qty,
            stop_loss=sl,
            take_profit=tp,
            risk_reward=rr,
            trailing_sl=0.0,
            order_id=order.get("id", ""),
            take_profit_1=tp1,
        )

        success = self.portfolio.open_position(position)
        if not success:
            logger.error(f"無法在帳戶中記錄持倉: {symbol}")
            return False

        # 掛交易所端條件單（paper 模式用虛擬條件單薄，真實模式用 API）
        close_side = "sell" if side == "buy" else "buy"

        # SL: 全倉保護
        sl_id = await self.exchange.place_stop_order(symbol, close_side, qty, sl, "sl")

        # TP1 (分批平倉 50%)
        tp1_id = ""
        if tp1 > 0:
            tp1_id = await self.exchange.place_stop_order(
                symbol, close_side, qty * 0.5, tp1, "tp1"
            )

        # TP2: 若有 TP1 只掛剩餘 50%，否則全倉
        tp2_qty = qty * 0.5 if tp1 > 0 else qty
        tp2_id = await self.exchange.place_stop_order(symbol, close_side, tp2_qty, tp, "tp2")

        # 把條件單 ID 存入持倉
        pos = self.portfolio.positions.get(symbol)
        if pos:
            pos.sl_order_id  = sl_id
            pos.tp1_order_id = tp1_id
            pos.tp2_order_id = tp2_id

        return True

    async def close_position(self, symbol: str, current_price: float,
                              reason: str = "manual") -> bool:
        """手動平倉"""
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            logger.warning(f"找不到 {symbol} 的持倉")
            return False

        close_side = "sell" if pos.side == "long" else "buy"
        order = await self.exchange.place_order(
            symbol=symbol,
            side=close_side,
            amount=pos.quantity,
            price=current_price,
            order_type="market",
        )

        if order:
            self.portfolio.close_position(symbol, current_price, reason)
            return True
        return False
