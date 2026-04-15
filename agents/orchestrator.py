"""
總指揮代理人 (OrchestratorAgent)
職責: 協調所有子代理人，運行主要交易循環
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import BotConfig
from core.exchange_client import ExchangeClient
from core.data_manager import DataManager
from core.portfolio import PaperPortfolio
from agents.gemini_client import GeminiClient
from agents.market_analyst import MarketAnalystAgent
from agents.signal_generator import SignalGeneratorAgent
from agents.risk_manager import RiskManagerAgent
from agents.execution_agent import ExecutionAgent
from agents.position_monitor import PositionMonitorAgent
from utils.logger import get_logger
from utils.helpers import format_usdt, format_pct, pct_change

logger = get_logger("Orchestrator")
console = Console()


class OrchestratorAgent:
    """
    AI 量化交易團隊總指揮

    代理人團隊:
    ┌─────────────────────────────────────────────┐
    │  OrchestratorAgent (總指揮)                  │
    │  ├─ MarketAnalystAgent    (市場分析)          │
    │  ├─ SignalGeneratorAgent  (信號生成)          │
    │  ├─ RiskManagerAgent      (風險管理)          │
    │  ├─ ExecutionAgent        (交易執行)          │
    │  └─ PositionMonitorAgent  (持倉監控)         │
    └─────────────────────────────────────────────┘
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._running = False
        self._cycle = 0

        # 初始化交易所客戶端
        self.exchange = ExchangeClient(
            name=cfg.exchange.name,
            api_key=cfg.exchange.api_key,
            api_secret=cfg.exchange.api_secret,
            testnet=cfg.exchange.testnet,
            paper_trading=cfg.exchange.paper_trading,
        )

        # 初始化模擬帳戶
        self.portfolio = PaperPortfolio(initial_balance=cfg.exchange.paper_balance)

        # 初始化數據管理器
        self.data_mgr = DataManager(
            exchange=self.exchange,
            primary_tf=cfg.trading.primary_timeframe,
            confirm_tfs=cfg.trading.confirmation_timeframes,
            ohlcv_limit=cfg.trading.ohlcv_limit,
        )

        # 初始化 Gemini 客戶端 (共享給需要 AI 的代理人)
        self.gemini = GeminiClient(
            api_key=cfg.agent.gemini_api_key,
            model=cfg.agent.gemini_model,
        )

        # 初始化各代理人
        self.market_analyst = MarketAnalystAgent(gemini=self.gemini)
        self.signal_generator = SignalGeneratorAgent(gemini=self.gemini)
        self.risk_manager = RiskManagerAgent(risk_config=cfg.risk)
        self.executor = ExecutionAgent(
            exchange=self.exchange,
            portfolio=self.portfolio,
        )
        self.monitor = PositionMonitorAgent(
            portfolio=self.portfolio,
            risk_manager=self.risk_manager,
            data_manager=self.data_mgr,
            trailing_stop=cfg.risk.trailing_stop,
            max_position_age_bars=cfg.risk.max_position_age_bars,
        )

    def _print_banner(self):
        """啟動橫幅"""
        mode = "🔴 真實交易" if not self.cfg.exchange.paper_trading else "🟢 模擬交易"
        exchange_info = f"{self.cfg.exchange.name.upper()} {'測試網' if self.cfg.exchange.testnet else '正式網'}"
        panel = Panel(
            f"[bold cyan]AI 量化交易機器人[/bold cyan]\n"
            f"模式: {mode} | 交易所: {exchange_info}\n"
            f"AI 引擎: {self.gemini.mode}\n"
            f"交易對: {', '.join(self.cfg.trading.symbols)}\n"
            f"時間框架: {self.cfg.trading.primary_timeframe} "
            f"(確認: {', '.join(self.cfg.trading.confirmation_timeframes)})\n\n"
            f"{self.risk_manager.summarize_risk_params()}",
            title="[bold yellow]⚡ 量化交易系統啟動[/bold yellow]",
            border_style="yellow",
        )
        console.print(panel)

    def _print_status_table(self):
        """打印當前帳戶和持倉狀態"""
        status = self.monitor.get_portfolio_status()
        stats = status["stats"]

        # 帳戶摘要
        equity = status["total_equity"]
        balance = status["available_balance"]
        total_pnl = stats["total_pnl"]
        total_pnl_pct = stats["total_pnl_pct"]

        # 主表格
        table = Table(
            title=f"📊 交易系統狀態 | 週期 #{self._cycle} | {datetime.now().strftime('%H:%M:%S')}",
            box=box.ROUNDED,
            border_style="cyan",
        )

        # 帳戶資訊行
        pnl_color = "green" if total_pnl >= 0 else "red"
        console.print(
            f"\n💰 總資產: [bold]{format_usdt(equity)}[/bold] | "
            f"可用: {format_usdt(balance)} | "
            f"總損益: [{pnl_color}]{format_usdt(total_pnl)} ({format_pct(total_pnl_pct)})[/{pnl_color}] | "
            f"勝率: [bold]{stats['win_rate']:.1f}%[/bold] | "
            f"已交易: {stats['closed_trades']} 筆 | "
            f"最大回撤: {stats['max_drawdown']:.2f}%"
        )

        # 持倉表格
        if status["positions"]:
            pos_table = Table(
                title="📋 當前持倉",
                box=box.SIMPLE,
                border_style="blue",
            )
            pos_table.add_column("交易對", style="cyan bold")
            pos_table.add_column("方向", justify="center")
            pos_table.add_column("進場價", justify="right")
            pos_table.add_column("當前價", justify="right")
            pos_table.add_column("止損", justify="right", style="red")
            pos_table.add_column("止盈", justify="right", style="green")
            pos_table.add_column("損益", justify="right")
            pos_table.add_column("損益%", justify="right")
            pos_table.add_column("盈虧比", justify="center")
            pos_table.add_column("持K數", justify="center")

            for p in status["positions"]:
                pnl_str = format_usdt(p["pnl"])
                pnl_pct_str = format_pct(p["pnl_pct"])
                color = "green" if p["pnl"] >= 0 else "red"
                side_color = "bright_green" if p["side"] == "long" else "bright_red"
                pos_table.add_row(
                    p["symbol"],
                    f"[{side_color}]{'↑ LONG' if p['side'] == 'long' else '↓ SHORT'}[/{side_color}]",
                    f"{p['entry']:.4f}",
                    f"{p['current']:.4f}",
                    f"{p['sl']:.4f}",
                    f"{p['tp']:.4f}",
                    f"[{color}]{pnl_str}[/{color}]",
                    f"[{color}]{pnl_pct_str}[/{color}]",
                    f"1:{p['rr']:.2f}",
                    str(p["bars_held"]),
                )
            console.print(pos_table)
        else:
            console.print("[dim]  (目前無持倉)[/dim]")

    async def run_cycle(self):
        """執行一個完整的交易週期"""
        self._cycle += 1

        # ── 1. 監控現有持倉 ──
        closed_trades = await self.monitor.monitor_all()
        if closed_trades:
            for t in closed_trades:
                color = "green" if t.pnl >= 0 else "red"
                console.print(
                    f"  [{color}]{'✅ 盈利' if t.pnl >= 0 else '❌ 虧損'}[/{color}] "
                    f"{t.symbol} {t.exit_reason.upper()} | "
                    f"損益: [{color}]{format_usdt(t.pnl)}[/{color}]"
                )

        # ── 2. 掃描每個交易對 ──
        for symbol in self.cfg.trading.symbols:

            # 已有持倉，跳過
            if symbol in self.portfolio.positions:
                continue

            # 日損/回撤檢查
            stats = self.portfolio.stats_summary()
            can_trade, reason = self.risk_manager.can_open_position(
                current_positions=self.portfolio.open_positions_count,
                daily_pnl_pct=self.portfolio.daily_pnl_pct(),
                drawdown_pct=stats["max_drawdown"],
                account_balance=self.portfolio.balance,
                required_notional=self.portfolio.balance * 0.1,  # 粗略檢查
            )
            if not can_trade:
                logger.warning(f"[風控] 暫停開倉: {reason}")
                break

            # ── 2a. 刷新市場數據 ──
            refreshed = await self.data_mgr.refresh(symbol)
            if not refreshed or not self.data_mgr.is_ready(symbol):
                logger.warning(f"{symbol} 數據不足，跳過")
                continue

            current_price = self.data_mgr.get_current_price(symbol)
            indicators = self.data_mgr.get_indicators(symbol)
            multi_tf = self.data_mgr.get_multi_tf_summary(symbol)
            atr = self.data_mgr.get_atr(symbol)

            if not indicators or atr <= 0:
                continue

            # ── 2b. 市場分析 ──
            analysis = await self.market_analyst.analyze(
                symbol=symbol,
                current_price=current_price,
                primary_indicators=indicators,
                multi_tf_data=multi_tf,
            )

            # 高風險環境直接跳過
            if analysis.get("risk_level") == "high":
                logger.info(f"[跳過] {symbol} 市場風險過高")
                continue

            # ── 2c. 信號生成 ──
            signal_result = await self.signal_generator.generate(
                symbol=symbol,
                market_analysis=analysis,
                indicators=indicators,
                current_price=current_price,
            )

            signal = signal_result.get("signal", "HOLD")
            confidence = float(signal_result.get("confidence", 0))

            if signal == "HOLD":
                continue

            # 信心分數門檻
            if confidence < self.cfg.agent.min_confidence:
                logger.info(
                    f"[跳過] {symbol} 信心分數 {confidence:.0f}% "
                    f"< 門檻 {self.cfg.agent.min_confidence:.0f}%"
                )
                continue

            # ── 2d. 風險管理 ──
            trade_setup = self.risk_manager.calculate_trade_setup(
                symbol=symbol,
                signal=signal,
                entry_price=current_price,
                atr=atr,
                account_balance=self.portfolio.balance,
                current_positions=self.portfolio.open_positions_count,
            )

            if trade_setup is None:
                continue

            # 精確資金充足性檢查
            can_open, block_reason = self.risk_manager.can_open_position(
                current_positions=self.portfolio.open_positions_count,
                daily_pnl_pct=self.portfolio.daily_pnl_pct(),
                drawdown_pct=stats["max_drawdown"],
                account_balance=self.portfolio.balance,
                required_notional=trade_setup["notional"],
            )
            if not can_open:
                logger.warning(f"[風控拒絕] {symbol}: {block_reason}")
                continue

            # ── 2e. 執行交易 ──
            success = await self.executor.execute_trade(trade_setup, confidence)
            if success:
                console.print(
                    f"\n  🚀 [bold green]開倉成功[/bold green] {symbol} "
                    f"[{'bright_green' if signal == 'LONG' else 'bright_red'}]{signal}[/] "
                    f"@ {current_price:.4f} | 盈虧比 1:{trade_setup['risk_reward']:.2f}\n"
                )

    async def run(self):
        """主循環"""
        self._print_banner()
        self._running = True

        logger.info(f"掃描間隔: {self.cfg.trading.scan_interval} 秒")
        logger.info("按 Ctrl+C 停止機器人\n")

        try:
            while self._running:
                try:
                    await self.run_cycle()
                    self._print_status_table()
                except Exception as e:
                    logger.error(f"交易週期發生錯誤: {e}", exc_info=True)

                await asyncio.sleep(self.cfg.trading.scan_interval)

        except KeyboardInterrupt:
            logger.info("收到停止信號...")
        finally:
            await self.stop()

    async def stop(self):
        """優雅停止"""
        self._running = False
        await self.exchange.close()

        # 最終報告
        stats = self.portfolio.stats_summary()
        console.print(
            Panel(
                f"[bold]最終績效報告[/bold]\n\n"
                f"初始資金:   {format_usdt(stats['initial_balance'])}\n"
                f"最終資金:   {format_usdt(stats['current_balance'])}\n"
                f"總損益:     [{'green' if stats['total_pnl'] >= 0 else 'red'}]"
                f"{format_usdt(stats['total_pnl'])} ({format_pct(stats['total_pnl_pct'])})"
                f"[/{'green' if stats['total_pnl'] >= 0 else 'red'}]\n"
                f"總交易數:   {stats['closed_trades']}\n"
                f"勝率:       {stats['win_rate']:.1f}%\n"
                f"平均盈虧比: 1:{stats['avg_risk_reward']:.2f}\n"
                f"獲利因子:   {stats['profit_factor']:.2f}\n"
                f"最大回撤:   {stats['max_drawdown']:.2f}%",
                title="[bold yellow]📊 交易機器人已停止[/bold yellow]",
                border_style="yellow",
            )
        )
