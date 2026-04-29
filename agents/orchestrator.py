"""
總指揮代理人 (OrchestratorAgent)
職責: 協調所有子代理人，運行主要交易循環
"""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

TW = timezone(timedelta(hours=8))
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
from utils.notifier import TelegramNotifier
from web.dashboard import start_dashboard, update_state

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
            passphrase=cfg.exchange.passphrase,
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

        # 通知 & 儀表板
        self.notifier = TelegramNotifier(
            token=cfg.telegram_token,
            chat_id=cfg.telegram_chat_id,
        )
        self._daily_report_hour = -1  # 每日報告追蹤

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
                label = "✂️ TP1分批" if t.exit_reason == "tp1" else ("✅ 盈利" if t.pnl >= 0 else "❌ 虧損")
                console.print(
                    f"  [{color}]{label}[/{color}] "
                    f"{t.symbol} {t.exit_reason.upper()} | "
                    f"損益: [{color}]{format_usdt(t.pnl)}[/{color}]"
                )
                self.notifier.notify_close(
                    t.symbol, t.side, t.entry_price, t.exit_price,
                    t.pnl, t.pnl_pct, t.exit_reason,
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

            # ── 2b. 規則分析 (免費，不消耗 Gemini 額度) ──
            rule_analysis = self.market_analyst._rule_analyze(
                symbol, current_price, indicators, multi_tf
            )

            if rule_analysis.get("risk_level") == "high":
                logger.info(f"[跳過] {symbol} 市場風險過高")
                continue

            # ── 2c. 規則信號 (免費預篩) ──
            rule_signal = self.signal_generator._rule_generate(
                symbol, rule_analysis, indicators, current_price
            )

            pre_signal = rule_signal.get("signal", "HOLD")
            pre_conf   = float(rule_signal.get("confidence", 0))

            # 規則說 HOLD 或信心不足 → 不呼叫 Gemini，直接跳過
            self._last_confidence = pre_conf
            if pre_signal == "HOLD" or pre_conf < self.cfg.agent.min_confidence:
                logger.debug(f"[預篩] {symbol} 規則HOLD，略過Gemini")
                continue

            # ── 2d. Gemini AI 確認 (只有規則發現信號時才呼叫) ──
            logger.info(f"[預篩通過] {symbol} {pre_signal} {pre_conf:.0f}% → 呼叫Gemini確認")
            if self.gemini.enabled:
                analysis = await self.market_analyst.analyze(
                    symbol=symbol, current_price=current_price,
                    primary_indicators=indicators, multi_tf_data=multi_tf,
                )
                signal_result = await self.signal_generator.generate(
                    symbol=symbol, market_analysis=analysis,
                    indicators=indicators, current_price=current_price,
                )
            else:
                analysis, signal_result = rule_analysis, rule_signal

            signal = signal_result.get("signal", "HOLD")
            confidence = float(signal_result.get("confidence", 0))

            if signal == "HOLD":
                continue

            # 信心分數門檻
            self._last_confidence = confidence
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

            # ── 2e. 資費率過濾 ──
            trade_setup = await self._apply_funding_filter(trade_setup, signal)
            if trade_setup is None:
                continue

            # ── 2f. 執行交易 ──
            success = await self.executor.execute_trade(trade_setup, confidence)
            if success:
                console.print(
                    f"\n  🚀 [bold green]開倉成功[/bold green] {symbol} "
                    f"[{'bright_green' if signal == 'LONG' else 'bright_red'}]{signal}[/] "
                    f"@ {current_price:.4f} | 盈虧比 1:{trade_setup['risk_reward']:.2f}\n"
                )
                # Telegram 通知開倉
                pos = self.portfolio.positions.get(symbol)
                self.notifier.notify_open(
                    symbol, pos.side if pos else signal.lower(),
                    trade_setup["entry_price"], trade_setup["stop_loss"],
                    trade_setup["take_profit"], trade_setup["quantity"],
                    trade_setup["risk_reward"], confidence,
                )

    async def _apply_funding_filter(self, setup: Dict, signal: str) -> Optional[Dict]:
        """
        資費率過濾器
        多頭且年化資費 > threshold: 縮倉 50% (市場擁擠多頭，持倉成本高)
        空頭且年化資費 < -threshold: 縮倉 50% (市場擁擠空頭)
        """
        threshold = self.cfg.risk.funding_rate_max_pct
        symbol = setup["symbol"]
        try:
            funding_pct = await self.exchange.fetch_funding_rate(symbol)
        except Exception:
            funding_pct = 0.0

        crowded_long  = signal == "LONG"  and funding_pct >  threshold
        crowded_short = signal == "SHORT" and funding_pct < -threshold

        if crowded_long or crowded_short:
            direction = "多頭擁擠" if crowded_long else "空頭擁擠"
            logger.warning(
                f"[資費率] {symbol} 年化 {funding_pct:.1f}% ({direction})，倉位縮減 50%"
            )
            reduced = dict(setup)
            reduced["quantity"] *= 0.5
            reduced["notional"] *= 0.5
            reduced["risk_amount"] *= 0.5
            return reduced

        if funding_pct != 0.0:
            logger.debug(f"[資費率] {symbol} 年化 {funding_pct:.1f}% → 正常")
        return setup

    async def _fast_price_update(self):
        """每 10 秒快速更新持倉價格和損益（不執行完整分析）"""
        while self._running:
            try:
                await asyncio.sleep(10)
                if not self.portfolio.positions:
                    continue
                t0 = time.monotonic()
                prices = {}
                for symbol in list(self.portfolio.positions.keys()):
                    ticker = await self.exchange.fetch_ticker(symbol)
                    p = ticker.get("last", 0)
                    if p > 0:
                        prices[symbol] = p
                        self.data_mgr.update_live_price(symbol, p)
                latency_ms = int((time.monotonic() - t0) * 1000)

                positions_info = []
                for symbol, pos in self.portfolio.positions.items():
                    price = prices.get(symbol, pos.entry_price)
                    positions_info.append({
                        "symbol":   symbol,
                        "side":     pos.side,
                        "entry":    pos.entry_price,
                        "current":  price,
                        "sl":       pos.trailing_sl if pos.trailing_sl > 0 else pos.stop_loss,
                        "tp":       pos.take_profit,
                        "rr":       pos.risk_reward,
                        "pnl":      pos.unrealized_pnl(price),
                        "pnl_pct":  pos.unrealized_pct(price),
                        "bars_held": pos.bars_held,
                    })

                total_equity = self.portfolio.total_equity(prices)
                stats = self.portfolio.stats_summary()
                update_state(
                    equity        = total_equity,
                    balance       = self.portfolio.balance,
                    total_pnl     = stats["total_pnl"],
                    total_pnl_pct = stats["total_pnl_pct"],
                    positions     = positions_info,
                    api_latency_ms= latency_ms,
                )
            except Exception as e:
                logger.debug(f"快速價格更新失敗: {e}")

    def _update_dashboard(self):
        """更新儀表板狀態（完整週期）"""
        status = self.monitor.get_portfolio_status()
        stats  = status["stats"]
        trades = [
            {
                "symbol":      t.symbol,
                "side":        t.side,
                "entry_price": t.entry_price,
                "exit_price":  t.exit_price,
                "pnl":         t.pnl,
                "pnl_pct":     t.pnl_pct,
                "exit_reason": t.exit_reason,
                "exit_time":   t.exit_time.astimezone(TW).strftime("%m/%d %H:%M"),
            }
            for t in self.portfolio.trade_history
        ]
        # 已實現資產 = 初始資金 + 所有已平倉損益（不含浮動）
        realized_equity = self.portfolio.initial_balance + stats["total_pnl"]

        update_state(
            cycle             = self._cycle,
            equity            = status["total_equity"],
            balance           = status["available_balance"],
            total_pnl         = stats["total_pnl"],
            total_pnl_pct     = stats["total_pnl_pct"],
            win_rate          = stats["win_rate"],
            closed_trades     = stats["closed_trades"],
            max_drawdown      = stats["max_drawdown"],
            positions         = status["positions"],
            recent_trades     = trades,
            gemini_connected  = self.gemini.enabled,
            last_confidence   = getattr(self, "_last_confidence", 0.0),
            realized_equity   = realized_equity,
        )

    async def run(self):
        """主循環"""
        self._print_banner()
        self._running = True

        # 啟動網頁儀表板
        start_dashboard(port=8080)

        # Telegram 啟動通知
        mode = "模擬交易" if self.cfg.exchange.paper_trading else "真實交易"
        self.notifier.notify_start(
            self.cfg.trading.symbols,
            self.portfolio.balance,
            mode,
        )

        logger.info(f"掃描間隔: {self.cfg.trading.scan_interval} 秒")
        logger.info(f"網頁儀表板: http://0.0.0.0:8080")
        logger.info("按 Ctrl+C 停止機器人\n")

        # 啟動快速價格更新 (每 10 秒)
        asyncio.create_task(self._fast_price_update())

        try:
            while self._running:
                try:
                    await self.run_cycle()
                    self._print_status_table()
                    self._update_dashboard()

                    # 每日報告 (台灣時間 00:00)
                    hour = datetime.now(TW).hour
                    if hour == 0 and self._daily_report_hour != 0:
                        self._daily_report_hour = 0
                        self.notifier.notify_daily_report(
                            self.portfolio.stats_summary()
                        )
                    elif hour != 0:
                        self._daily_report_hour = hour

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
