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
        )

        success = self.portfolio.open_position(position)
        if not success:
            logger.error(f"無法在帳戶中記錄持倉: {symbol}")
            return False

        # 真實交易所: 設定 SL/TP 止損單
        if not self.exchange.paper_trading:
            await self._place_sl_tp_orders(symbol, side, qty, sl, tp)

        return True

    async def _place_sl_tp_orders(self, symbol: str, entry_side: str,
                                   qty: float, sl: float, tp: float):
        """在交易所設定止損止盈單 (真實交易用)"""
        close_side = "sell" if entry_side == "buy" else "buy"
        try:
            # 止損單
            sl_order = await self.exchange.place_order(
                symbol=symbol, side=close_side, amount=qty,
                price=sl, order_type="stop_loss"
            )
            logger.info(f"止損單已設定: {sl:.4f} | ID: {sl_order.get('id')}")

            # 止盈單
            tp_order = await self.exchange.place_order(
                symbol=symbol, side=close_side, amount=qty,
                price=tp, order_type="take_profit"
            )
            logger.info(f"止盈單已設定: {tp:.4f} | ID: {tp_order.get('id')}")

        except Exception as e:
            logger.error(f"設定止損/止盈單失敗: {e}")

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
