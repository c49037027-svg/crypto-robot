"""
模擬交易帳戶 (Paper Trading)
追蹤持倉、P&L、風險指標
真實交易模式下僅記錄歷史
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from utils.logger import get_logger
from utils.helpers import format_usdt, format_pct, pct_change

logger = get_logger("Portfolio")


@dataclass
class Position:
    symbol: str
    side: str                # 'long' | 'short'
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bars_held: int = 0
    trailing_sl: float = 0.0  # 移動止損當前價位
    order_id: str = ""
    strategy: str = "multi_signal"

    @property
    def notional(self) -> float:
        return self.entry_price * self.quantity

    def unrealized_pnl(self, current_price: float) -> float:
        if self.side == "long":
            return (current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - current_price) * self.quantity

    def unrealized_pct(self, current_price: float) -> float:
        return pct_change(self.entry_price, current_price) * (1 if self.side == "long" else -1)

    def is_sl_hit(self, current_price: float) -> bool:
        sl = self.trailing_sl if self.trailing_sl > 0 else self.stop_loss
        if self.side == "long":
            return current_price <= sl
        else:
            return current_price >= sl

    def is_tp_hit(self, current_price: float) -> bool:
        if self.side == "long":
            return current_price >= self.take_profit
        else:
            return current_price <= self.take_profit

    def risk_amount(self) -> float:
        return abs(self.entry_price - self.stop_loss) * self.quantity

    def potential_reward(self) -> float:
        return abs(self.take_profit - self.entry_price) * self.quantity


@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str  # 'sl' | 'tp' | 'manual' | 'timeout'
    risk_reward: float
    strategy: str

    @property
    def duration_hours(self) -> float:
        delta = self.exit_time - self.entry_time
        return delta.total_seconds() / 3600


class PaperPortfolio:
    """
    模擬交易帳戶
    - 強制執行止損/止盈
    - 追蹤所有交易歷史
    - 計算績效指標
    """

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance        # 可用 USDT
        self.peak_balance = initial_balance   # 歷史最高資產 (用於計算回撤)
        self.positions: Dict[str, Position] = {}  # symbol -> Position
        self.trade_history: List[Trade] = []
        self.daily_start_balance: float = initial_balance
        self._today_str: str = self._today()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _reset_daily_if_needed(self):
        today = self._today()
        if today != self._today_str:
            self._today_str = today
            self.daily_start_balance = self.total_equity()

    @property
    def open_positions_count(self) -> int:
        return len(self.positions)

    def total_equity(self, prices: Dict[str, float] = None) -> float:
        """總資產 = 可用資金 + 所有未平倉帳面值"""
        equity = self.balance
        if prices:
            for sym, pos in self.positions.items():
                price = prices.get(sym, pos.entry_price)
                equity += pos.notional + pos.unrealized_pnl(price)
        else:
            for pos in self.positions.values():
                equity += pos.notional
        return equity

    def daily_pnl_pct(self) -> float:
        equity = self.total_equity()
        return pct_change(self.daily_start_balance, equity)

    def max_drawdown(self) -> float:
        if not self.trade_history:
            return 0.0
        equity = self.initial_balance
        peak = equity
        max_dd = 0.0
        for t in self.trade_history:
            equity += t.pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def win_rate(self) -> float:
        if not self.trade_history:
            return 0.0
        wins = sum(1 for t in self.trade_history if t.pnl > 0)
        return wins / len(self.trade_history) * 100

    def average_rr(self) -> float:
        if not self.trade_history:
            return 0.0
        return sum(t.risk_reward for t in self.trade_history) / len(self.trade_history)

    def open_position(self, position: Position) -> bool:
        """開倉"""
        self._reset_daily_if_needed()

        if position.symbol in self.positions:
            logger.warning(f"{position.symbol} 已有持倉，略過")
            return False

        cost = position.notional
        if cost > self.balance:
            logger.warning(f"資金不足: 需要 {cost:.2f} USDT，可用 {self.balance:.2f} USDT")
            return False

        self.balance -= cost
        self.positions[position.symbol] = position

        logger.info(
            f"[開倉] {position.symbol} {position.side.upper()} "
            f"@ {position.entry_price:.4f} | "
            f"數量: {position.quantity:.6f} | "
            f"SL: {position.stop_loss:.4f} | "
            f"TP: {position.take_profit:.4f} | "
            f"盈虧比: 1:{position.risk_reward:.2f}"
        )
        return True

    def close_position(self, symbol: str, exit_price: float,
                       exit_reason: str = "manual") -> Optional[Trade]:
        """平倉"""
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        pnl = pos.unrealized_pnl(exit_price)
        pnl_pct = pos.unrealized_pct(exit_price)
        self.balance += pos.notional + pnl

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        trade = Trade(
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_time=pos.entry_time,
            exit_time=datetime.now(timezone.utc),
            exit_reason=exit_reason,
            risk_reward=pos.risk_reward,
            strategy=pos.strategy,
        )
        self.trade_history.append(trade)

        emoji = "✅" if pnl >= 0 else "❌"
        reason_map = {"sl": "止損", "tp": "止盈", "manual": "手動", "timeout": "超時"}
        logger.info(
            f"{emoji} [平倉] {symbol} {exit_reason.upper()} @ {exit_price:.4f} | "
            f"損益: {format_usdt(pnl)} ({format_pct(pnl_pct)})"
        )
        return trade

    def update_trailing_stop(self, symbol: str, current_price: float, atr: float,
                              atr_mult: float = 1.5):
        """更新移動止損"""
        pos = self.positions.get(symbol)
        if pos is None:
            return

        if pos.side == "long":
            new_sl = current_price - atr_mult * atr
            if new_sl > (pos.trailing_sl if pos.trailing_sl > 0 else pos.stop_loss):
                pos.trailing_sl = new_sl
        else:
            new_sl = current_price + atr_mult * atr
            if new_sl < (pos.trailing_sl if pos.trailing_sl > 0 else pos.stop_loss):
                pos.trailing_sl = new_sl

    def stats_summary(self) -> Dict:
        """績效統計摘要"""
        total_pnl = sum(t.pnl for t in self.trade_history)
        closed = len(self.trade_history)
        wins = sum(1 for t in self.trade_history if t.pnl > 0)
        losses = closed - wins

        avg_win = (
            sum(t.pnl for t in self.trade_history if t.pnl > 0) / wins
            if wins > 0 else 0
        )
        avg_loss = (
            sum(t.pnl for t in self.trade_history if t.pnl < 0) / losses
            if losses > 0 else 0
        )
        profit_factor = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else float("inf")

        return {
            "initial_balance": self.initial_balance,
            "current_balance": self.balance,
            "total_pnl": total_pnl,
            "total_pnl_pct": pct_change(self.initial_balance, self.balance),
            "open_positions": self.open_positions_count,
            "closed_trades": closed,
            "win_rate": self.win_rate(),
            "avg_win_usdt": avg_win,
            "avg_loss_usdt": avg_loss,
            "profit_factor": profit_factor,
            "max_drawdown": self.max_drawdown(),
            "avg_risk_reward": self.average_rr(),
        }
