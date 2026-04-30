"""
風險管理代理人 (RiskManagerAgent)
職責: 強制執行 SL/TP 計算、盈虧比驗證、倉位計算
這是最重要的安全關卡 - 任何不符合風險參數的交易都會被拒絕
"""
from typing import Dict, Optional, Tuple
from config import RiskConfig
from core.portfolio import Position
from utils.logger import get_logger
from utils.helpers import format_usdt, format_pct

logger = get_logger("RiskManager")


class RiskManagerAgent:
    """
    風險管理代理人 (純數學計算，不依賴 AI)
    
    核心邏輯:
    1. 計算 ATR-based 止損位
    2. 計算止盈位確保 R:R >= 1.5
    3. 計算最大倉位大小
    4. 驗證日損限額和回撤限額
    5. 拒絕不符合風險規則的交易
    """

    def __init__(self, risk_config: RiskConfig):
        self.cfg = risk_config

    def calculate_trade_setup(
        self,
        symbol: str,
        signal: str,           # 'LONG' | 'SHORT'
        entry_price: float,
        atr: float,
        account_balance: float,
        current_positions: int,
    ) -> Optional[Dict]:
        """
        計算完整的交易設定
        
        返回:
          None 如果不符合風險規則
          Dict 包含 stop_loss, take_profit, quantity, risk_reward, risk_amount
        """
        if signal not in ("LONG", "SHORT"):
            return None

        # 1. 計算止損和止盈
        sl_distance  = self.cfg.atr_sl_multiplier  * atr
        tp1_distance = self.cfg.atr_tp1_multiplier * atr  # TP1: 1.5 ATR
        tp_distance  = self.cfg.atr_tp_multiplier  * atr  # TP2: 3.0 ATR

        if signal == "LONG":
            stop_loss   = entry_price - sl_distance
            take_profit_1 = entry_price + tp1_distance
            take_profit = entry_price + tp_distance
        else:
            stop_loss   = entry_price + sl_distance
            take_profit_1 = entry_price - tp1_distance
            take_profit = entry_price - tp_distance

        # 2. 驗證盈虧比
        actual_rr = tp_distance / sl_distance if sl_distance > 0 else 0
        if actual_rr < self.cfg.min_risk_reward:
            logger.warning(
                f"[拒絕] {symbol} 盈虧比 1:{actual_rr:.2f} < 最低要求 1:{self.cfg.min_risk_reward}"
            )
            return None

        # 3. 計算倉位大小 (限制名義值不超過帳戶餘額 90%)
        risk_amount = account_balance * (self.cfg.risk_per_trade_pct / 100)
        quantity = risk_amount / sl_distance if sl_distance > 0 else 0

        if quantity <= 0:
            logger.warning(f"[拒絕] {symbol} 計算倉位為 0")
            return None

        # 每筆倉位名義值上限 = 帳戶餘額 / 最大持倉數 * 0.9
        max_notional = account_balance / max(self.cfg.max_positions, 1) * 0.9
        if entry_price * quantity > max_notional:
            quantity = max_notional / entry_price

        # 4. 確認止損和止盈都有意義
        if stop_loss <= 0 or take_profit <= 0:
            logger.warning(f"[拒絕] {symbol} 止損/止盈價格無效")
            return None

        notional = entry_price * quantity

        logger.info(
            f"[風控通過] {symbol} {signal} | "
            f"進場: {entry_price:.4f} | "
            f"SL: {stop_loss:.4f} (-{sl_distance:.4f}) | "
            f"TP: {take_profit:.4f} (+{tp_distance:.4f}) | "
            f"盈虧比: 1:{actual_rr:.2f} | "
            f"倉位: {quantity:.6f} ({format_usdt(notional)}) | "
            f"風險: {format_usdt(risk_amount)}"
        )

        return {
            "symbol":        symbol,
            "signal":        signal,
            "entry_price":   entry_price,
            "stop_loss":     stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit":   take_profit,
            "sl_distance":   sl_distance,
            "tp1_distance":  tp1_distance,
            "tp_distance":   tp_distance,
            "risk_reward":   actual_rr,
            "quantity":      quantity,
            "notional":      notional,
            "risk_amount":   risk_amount,
            "atr":           atr,
        }

    def can_open_position(
        self,
        current_positions: int,
        daily_pnl_pct: float,
        drawdown_pct: float,
        account_balance: float,
        required_notional: float,
    ) -> Tuple[bool, str]:
        """
        檢查是否允許開倉
        返回 (允許開倉, 拒絕原因)
        """
        # 持倉數量檢查
        if current_positions >= self.cfg.max_positions:
            return False, f"已達最大持倉數 {self.cfg.max_positions}"

        # 日損限額檢查
        if daily_pnl_pct <= -self.cfg.max_daily_loss_pct:
            return False, f"每日虧損 {daily_pnl_pct:.2f}% 已達限額 -{self.cfg.max_daily_loss_pct}%"

        # 回撤限額檢查
        if drawdown_pct >= self.cfg.max_drawdown_pct:
            return False, f"最大回撤 {drawdown_pct:.2f}% 已達限額 {self.cfg.max_drawdown_pct}%"

        # 資金充足性檢查
        if required_notional > account_balance * 0.95:
            return False, f"資金不足 (需要 {required_notional:.2f}, 可用 {account_balance:.2f})"

        return True, ""

    def calculate_trailing_stop(self, signal: str, current_price: float, atr: float,
                                 current_sl: float) -> float:
        """
        計算新的移動止損位
        只往有利方向移動，不會退步
        """
        new_sl_distance = self.cfg.trailing_stop_atr_mult * atr
        if signal == "LONG":
            new_sl = current_price - new_sl_distance
            return max(new_sl, current_sl)  # 只上移
        else:
            new_sl = current_price + new_sl_distance
            return min(new_sl, current_sl)  # 只下移

    def summarize_risk_params(self) -> str:
        return (
            f"風險設定摘要:\n"
            f"  每筆風險: {self.cfg.risk_per_trade_pct}% | "
            f"最大持倉: {self.cfg.max_positions} | "
            f"最低盈虧比: 1:{self.cfg.min_risk_reward}\n"
            f"  止損倍數: {self.cfg.atr_sl_multiplier}x ATR | "
            f"止盈倍數: {self.cfg.atr_tp_multiplier}x ATR\n"
            f"  日損限額: {self.cfg.max_daily_loss_pct}% | "
            f"最大回撤: {self.cfg.max_drawdown_pct}% | "
            f"移動止損: {'開' if self.cfg.trailing_stop else '關'}"
        )
