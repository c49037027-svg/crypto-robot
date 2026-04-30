"""
事件驅動回測引擎
逐 K 線模擬純規則策略（不呼叫 Gemini，避免費用與延遲）
支援: TP1 分批停利、保本止損、超時出場、手續費/滑價
"""
import ccxt
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.indicators import compute_indicators, get_latest
from agents.market_analyst import MarketAnalystAgent
from agents.signal_generator import SignalGeneratorAgent
from agents.risk_manager import RiskManagerAgent
from config import RiskConfig
from utils.logger import get_logger

logger = get_logger("Backtester")

WARMUP = 210  # EMA200 需要至少 200 根暖機


# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

@dataclass
class BtPosition:
    symbol: str
    side: str              # 'long' | 'short'
    entry_price: float
    quantity: float
    initial_quantity: float
    stop_loss: float
    take_profit_1: float
    take_profit: float
    entry_bar: int
    tp1_hit: bool = False
    bars_held: int = 0
    indicators: dict = field(default_factory=dict)  # 進場時指標快照


@dataclass
class BtTrade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    bars_held: int
    exit_reason: str                               # sl | tp1 | tp | timeout
    trade_id: int = 0                              # 同一次進場的 TP1+最終出場共享 id
    entry_indicators: dict = field(default_factory=dict)


# ─────────────────────────────────────────────
# 回測引擎
# ─────────────────────────────────────────────

class Backtester:
    """
    逐 K 線事件驅動回測

    費用假設 (每單邊):
      手續費: 0.05% (Taker)
      滑價:   0.10%
      合計:   0.15% per side = 往返 0.30%
    """

    FEE_RATE = 0.0015  # 0.05% taker + 0.10% slippage per side

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_balance: float = 10_000.0,
        risk_config: RiskConfig = None,
        min_confidence: float = 65.0,
        max_position_age_bars: int = 168,
    ):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_cfg = risk_config or RiskConfig()
        self.min_confidence = min_confidence
        self.max_age_bars = max_position_age_bars

        self.position: Optional[BtPosition] = None
        self.trades: List[BtTrade] = []
        self.equity_curve: List[float] = []
        self.total_fees: float = 0.0

        self._analyst = MarketAnalystAgent()
        self._signal  = SignalGeneratorAgent()
        self._risk    = RiskManagerAgent(self.risk_cfg)

    # ─────────────────────────────────────────
    # 資料取得
    # ─────────────────────────────────────────

    @staticmethod
    def fetch_data(symbol: str, days: int = 365) -> pd.DataFrame:
        """從 Binance 公開接口抓取 1h OHLCV（同步）"""
        limit = min(days * 24, 1000)
        ex = ccxt.binance({"enableRateLimit": True})
        bars = ex.fetch_ohlcv(symbol, "1h", limit=limit)
        df = pd.DataFrame(
            bars, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("timestamp").astype(float)
        logger.info(
            f"載入 {symbol}: {len(df)} 根 1h K 線 "
            f"({df.index[0].date()} → {df.index[-1].date()})"
        )
        return df

    @staticmethod
    def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        return df.resample(rule, closed="right", label="right").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        ).dropna()

    # ─────────────────────────────────────────
    # 主回測迴圈
    # ─────────────────────────────────────────

    def run(self, df_1h: pd.DataFrame) -> List[BtTrade]:
        """執行回測，返回所有交易記錄"""
        df_4h = self._resample(df_1h, "4h")
        df_1d = self._resample(df_1h, "1D")

        ind_1h = compute_indicators(df_1h)
        ind_4h = compute_indicators(df_4h)
        ind_1d = compute_indicators(df_1d)

        n = len(df_1h)
        self.balance    = self.initial_balance
        self.position   = None
        self.trades     = []
        self.equity_curve = []
        self.total_fees = 0.0

        for i in range(WARMUP, n):
            bar = df_1h.iloc[i]
            ts  = df_1h.index[i]

            # ── 持倉監控 ──
            if self.position is not None:
                fully_closed = self._check_bar(bar, i)
                if fully_closed:
                    self.position = None

            # ── 開倉嘗試 ──
            if self.position is None:
                latest = {k: float(v.iloc[i]) for k, v in ind_1h.items()}
                price  = bar["close"]

                p4h = int(df_4h.index.searchsorted(ts, side="right")) - 1
                p1d = int(df_1d.index.searchsorted(ts, side="right")) - 1
                latest_4h = ({k: float(v.iloc[p4h]) for k, v in ind_4h.items()}
                             if p4h >= 0 else {})
                latest_1d = ({k: float(v.iloc[p1d]) for k, v in ind_1d.items()}
                             if p1d >= 0 else {})
                multi_tf = {"4h": latest_4h, "1d": latest_1d}

                analysis = self._analyst._rule_analyze(
                    self.symbol, price, latest, multi_tf
                )
                if analysis.get("risk_level") == "high":
                    self._record_equity(bar)
                    continue

                sig_result = self._signal._rule_generate(
                    self.symbol, analysis, latest, price
                )
                signal = sig_result.get("signal", "HOLD")
                conf   = float(sig_result.get("confidence", 0))

                if signal != "HOLD" and conf >= self.min_confidence:
                    atr   = latest.get("atr", price * 0.02)
                    setup = self._risk.calculate_trade_setup(
                        symbol=self.symbol, signal=signal,
                        entry_price=price, atr=atr,
                        account_balance=self.balance, current_positions=0,
                    )
                    if setup:
                        self._open(setup, i, latest)

            self._record_equity(bar)

        # 強制平未結倉位
        if self.position is not None:
            last = df_1h.iloc[-1]
            self._close(last["close"], len(df_1h) - 1, "timeout")
            self.position = None

        return self.trades

    # ─────────────────────────────────────────
    # 持倉邏輯
    # ─────────────────────────────────────────

    def _open(self, setup: Dict, bar_idx: int, indicators: Dict):
        qty  = setup["quantity"]
        cost = setup["entry_price"] * qty
        if cost > self.balance * 0.95:
            qty = self.balance * 0.9 / setup["entry_price"]

        # 進場手續費 + 滑價
        fee = setup["entry_price"] * qty * self.FEE_RATE
        self.balance -= setup["entry_price"] * qty + fee
        self.total_fees += fee

        self.position = BtPosition(
            symbol=self.symbol,
            side="long" if setup["signal"] == "LONG" else "short",
            entry_price=setup["entry_price"],
            quantity=qty,
            initial_quantity=qty,
            stop_loss=setup["stop_loss"],
            take_profit_1=setup.get("take_profit_1", 0.0),
            take_profit=setup["take_profit"],
            entry_bar=bar_idx,
            indicators=indicators,
        )

    def _check_bar(self, bar, bar_idx: int) -> bool:
        """根據當根 K 線 high/low 偵測 SL/TP 觸發，返回 True = 全平"""
        pos = self.position
        pos.bars_held += 1
        hi, lo, cl = bar["high"], bar["low"], bar["close"]

        if pos.side == "long":
            if not pos.tp1_hit and pos.take_profit_1 > 0 and hi >= pos.take_profit_1:
                self._partial_close(pos.take_profit_1, pos)
            if hi >= pos.take_profit:
                return self._close(pos.take_profit, bar_idx, "tp")
            if lo <= pos.stop_loss:
                return self._close(pos.stop_loss, bar_idx, "sl")
        else:
            if not pos.tp1_hit and pos.take_profit_1 > 0 and lo <= pos.take_profit_1:
                self._partial_close(pos.take_profit_1, pos)
            if lo <= pos.take_profit:
                return self._close(pos.take_profit, bar_idx, "tp")
            if hi >= pos.stop_loss:
                return self._close(pos.stop_loss, bar_idx, "sl")

        if pos.bars_held >= self.max_age_bars:
            return self._close(cl, bar_idx, "timeout")

        return False

    def _partial_close(self, exit_price: float, pos: BtPosition):
        """TP1 分批平倉 50%，止損移至成本"""
        qty_close = min(pos.initial_quantity * self.risk_cfg.tp1_close_pct, pos.quantity)
        if qty_close <= 0:
            return

        if pos.side == "long":
            pnl = (exit_price - pos.entry_price) * qty_close
        else:
            pnl = (pos.entry_price - exit_price) * qty_close

        # 出場手續費
        fee = exit_price * qty_close * self.FEE_RATE
        self.balance += pos.entry_price * qty_close + pnl - fee
        self.total_fees += fee

        pos.quantity  -= qty_close
        pos.stop_loss  = pos.entry_price
        pos.tp1_hit    = True

        cost_basis = pos.entry_price * qty_close
        self.trades.append(BtTrade(
            symbol=pos.symbol, side=pos.side,
            entry_price=pos.entry_price, exit_price=exit_price,
            quantity=qty_close,
            pnl=pnl - fee,
            pnl_pct=(pnl - fee) / cost_basis * 100 if cost_basis > 0 else 0,
            bars_held=pos.bars_held, exit_reason="tp1",
            trade_id=pos.entry_bar,
            entry_indicators=pos.indicators,
        ))

    def _close(self, exit_price: float, bar_idx: int, reason: str) -> bool:
        """全平剩餘倉位"""
        pos = self.position
        if pos is None:
            return False

        if pos.side == "long":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        fee = exit_price * pos.quantity * self.FEE_RATE
        cost_basis = pos.entry_price * pos.quantity
        self.balance += cost_basis + pnl - fee
        self.total_fees += fee

        self.trades.append(BtTrade(
            symbol=pos.symbol, side=pos.side,
            entry_price=pos.entry_price, exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl - fee,
            pnl_pct=(pnl - fee) / cost_basis * 100 if cost_basis > 0 else 0,
            bars_held=pos.bars_held, exit_reason=reason,
            trade_id=pos.entry_bar,
            entry_indicators=pos.indicators,
        ))
        return True

    def _record_equity(self, bar):
        """記錄當根收盤時的總資產（含未實現損益，扣除持倉成本）"""
        if self.position:
            pos = self.position
            if pos.side == "long":
                unrealized = (bar["close"] - pos.entry_price) * pos.quantity
            else:
                unrealized = (pos.entry_price - bar["close"]) * pos.quantity
            equity = self.balance + pos.entry_price * pos.quantity + unrealized
        else:
            equity = self.balance
        self.equity_curve.append(equity)

    # ─────────────────────────────────────────
    # 績效報告
    # ─────────────────────────────────────────

    def report(self) -> str:
        trades = self.trades
        if not trades:
            return "無交易記錄"

        # ── 依 trade_id 分組，正確計算勝率 ──
        # Full Win:     TP2 命中 (exit_reason=="tp")
        # Partial Win:  TP1 命中 + 最終出場 >= 成本 (SL移保本後小盈或平手)
        # Full Loss:    SL在TP1前命中 或 timeout無TP1

        groups: Dict[int, List] = {}
        for t in trades:
            groups.setdefault(t.trade_id, []).append(t)

        full_win = partial_win = full_loss = 0
        group_pnls = []
        group_sides = []

        for tid, grp in groups.items():
            reasons_in_grp = {t.exit_reason for t in grp}
            total_group_pnl = sum(t.pnl for t in grp)
            side = grp[0].side
            group_pnls.append(total_group_pnl)
            group_sides.append(side)

            if "tp" in reasons_in_grp:
                full_win += 1
            elif "tp1" in reasons_in_grp:
                partial_win += 1
            else:
                full_loss += 1

        n_groups = len(groups)
        win_rate = (full_win + partial_win) / n_groups * 100 if n_groups else 0

        total_pnl    = sum(t.pnl for t in trades)
        final_equity = self.balance
        total_return = (final_equity - self.initial_balance) / self.initial_balance * 100

        gross_profit = sum(p for p in group_pnls if p > 0)
        gross_loss   = abs(sum(p for p in group_pnls if p <= 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        avg_win  = gross_profit / sum(1 for p in group_pnls if p > 0) if any(p > 0 for p in group_pnls) else 0
        avg_loss = gross_loss   / sum(1 for p in group_pnls if p <= 0) if any(p <= 0 for p in group_pnls) else 0

        # 持倉時間以最終出場的 bars_held 為準
        final_trades = [t for t in trades if t.exit_reason != "tp1"]
        avg_bars = sum(t.bars_held for t in final_trades) / len(final_trades) if final_trades else 0

        # 最大回撤（以 group pnl 序列計算）
        equity = self.initial_balance
        peak   = equity
        max_dd = 0.0
        for p in group_pnls:
            equity += p
            peak    = max(peak, equity)
            dd      = (peak - equity) / peak * 100
            max_dd  = max(max_dd, dd)

        # Sharpe（逐根 K 線資產曲線，年化以 1h 為基礎 × √8760）
        if len(self.equity_curve) > 2:
            eq   = pd.Series(self.equity_curve)
            rets = eq.pct_change().dropna()
            sharpe = (rets.mean() / rets.std() * (8760 ** 0.5)) if rets.std() > 0 else 0
        else:
            sharpe = 0

        # 多空分開勝率
        long_grps  = [(p, s) for p, s in zip(group_pnls, group_sides) if s == "long"]
        short_grps = [(p, s) for p, s in zip(group_pnls, group_sides) if s == "short"]
        long_wr  = sum(1 for p, _ in long_grps  if p > 0) / len(long_grps)  * 100 if long_grps  else 0
        short_wr = sum(1 for p, _ in short_grps if p > 0) / len(short_grps) * 100 if short_grps else 0

        reasons: Dict[str, int] = {}
        for t in trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

        sign     = "+" if total_return >= 0 else ""
        pnl_sign = "+" if total_pnl    >= 0 else ""

        lines = [
            "=" * 58,
            f"  回測報告: {self.symbol}",
            "=" * 58,
            f"  初始資金        {self.initial_balance:>12,.2f} USDT",
            f"  最終資金        {final_equity:>12,.2f} USDT",
            f"  總報酬          {sign}{total_return:>11.2f}%",
            f"  總損益          {pnl_sign}{total_pnl:>11.2f} USDT",
            f"  手續費+滑價     {-self.total_fees:>+12.2f} USDT  (每單邊 {self.FEE_RATE*100:.2f}%)",
            "-" * 58,
            f"  總交易次數      {n_groups:>12}  (獨立進場)",
            f"    完整獲利      {full_win:>12}  (TP1+TP2 全中)",
            f"    部分獲利      {partial_win:>12}  (TP1+保本出場)",
            f"    虧損          {full_loss:>12}  (SL/timeout 無TP1)",
            f"  勝率            {win_rate:>11.1f}%  (完整+部分)",
            f"    多頭勝率      {long_wr:>11.1f}%  ({len(long_grps)} 次)",
            f"    空頭勝率      {short_wr:>11.1f}%  ({len(short_grps)} 次)",
            f"  平均獲利        {avg_win:>+12.2f} USDT",
            f"  平均虧損        {avg_loss:>-12.2f} USDT",
            f"  獲利因子        {profit_factor:>12.2f}",
            f"  最大回撤        {max_dd:>11.2f}%",
            f"  Sharpe (年化)   {sharpe:>12.2f}",
            f"  平均持倉K數     {avg_bars:>11.1f}",
            "-" * 58,
            "  出場原因:",
        ]
        reason_map = {"sl": "止損", "tp1": "TP1分批", "tp": "止盈", "timeout": "超時"}
        for r, cnt in sorted(reasons.items()):
            label = reason_map.get(r, r)
            pct   = cnt / len(trades) * 100
            lines.append(f"    {label:<10} {cnt:3} 次  ({pct:.0f}%)")
        lines.append("=" * 58)

        return "\n".join(lines)

    # ─────────────────────────────────────────
    # 虧損單診斷
    # ─────────────────────────────────────────

    def debug_losers(self, n_each: int = 3) -> str:
        """
        印出 n_each 筆虧損多單 + n_each 筆虧損空單的進場指標
        用於診斷為什麼系統產生低品質訊號
        """
        losing_longs  = [t for t in self.trades
                         if t.side == "long"  and t.pnl < 0 and t.exit_reason != "tp1"]
        losing_shorts = [t for t in self.trades
                         if t.side == "short" and t.pnl < 0 and t.exit_reason != "tp1"]

        lines = ["\n" + "=" * 58, "  🔍 虧損單指標診斷", "=" * 58]

        for label, group in [("虧損多單 (LONG)", losing_longs[:n_each]),
                              ("虧損空單 (SHORT)", losing_shorts[:n_each])]:
            lines.append(f"\n  ── {label} ──")
            if not group:
                lines.append("    (無資料)")
                continue
            for i, t in enumerate(group, 1):
                ind = t.entry_indicators
                price = t.entry_price
                bb_u  = ind.get("bb_upper", 0)
                bb_m  = ind.get("bb_mid",   0)
                bb_l  = ind.get("bb_lower", 0)
                rsi   = ind.get("rsi",      0)
                ema9  = ind.get("ema9",     0)
                ema21 = ind.get("ema21",    0)
                ema55 = ind.get("ema55",    0)
                ema200= ind.get("ema200",   0)
                atr   = ind.get("atr",      0)
                adx   = ind.get("adx",      0)
                vol   = ind.get("volume",   0)
                vsma  = ind.get("vol_sma20",1)
                vol_r = vol / vsma if vsma > 0 else 0

                bb_pos = (price - bb_l) / (bb_u - bb_l) * 100 if bb_u > bb_l else 50
                lines += [
                    f"  [{i}] 進場 {price:.2f} → 出場 {t.exit_price:.2f}  損益 {t.pnl:+.2f} USDT  ({t.exit_reason})",
                    f"      RSI={rsi:.1f}  ADX={adx:.1f}  成交量={vol_r:.1f}x均量",
                    f"      EMA排列: 9={'>' if ema9>ema21 else '<'}21={'>' if ema21>ema55 else '<'}55={'>' if ema55>ema200 else '<'}200",
                    f"      BB位置: {bb_pos:.0f}% (上={bb_u:.1f} 中={bb_m:.1f} 下={bb_l:.1f})",
                    f"      ATR={atr:.2f} ({atr/price*100:.2f}%)",
                    f"      {'⚠ 價格>BB上軌' if price > bb_u else ('⚠ 價格<BB下軌' if price < bb_l else '  價格在BB帶內')}",
                    f"      {'⚠ RSI超買>65' if rsi>65 else ('⚠ RSI超賣<35' if rsi<35 else '  RSI正常')}",
                ]
        lines.append("=" * 58)
        return "\n".join(lines)

    def export_csv(self, path: str):
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["side", "entry_price", "exit_price", "quantity",
                        "pnl", "pnl_pct", "bars_held", "exit_reason"])
            for t in self.trades:
                w.writerow([
                    t.side, f"{t.entry_price:.4f}", f"{t.exit_price:.4f}",
                    f"{t.quantity:.6f}", f"{t.pnl:.2f}", f"{t.pnl_pct:.2f}",
                    t.bars_held, t.exit_reason,
                ])
