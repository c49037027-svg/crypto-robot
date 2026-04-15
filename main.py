"""
AI 量化交易機器人 - 主程式入口
使用方式:
  python main.py              # 啟動交易機器人
  python main.py --check      # 只檢查配置，不交易
  python main.py --backtest   # 執行回測模式
"""
import asyncio
import sys
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import config
from agents.orchestrator import OrchestratorAgent
from utils.logger import get_logger

console = Console()
logger = get_logger("Main")


def check_config():
    """檢查配置是否正確"""
    console.print("\n[bold]🔍 配置檢查...[/bold]")
    issues = config.validate()

    if issues:
        for issue in issues:
            console.print(f"  {issue}")
        return False

    # 顯示配置摘要
    table = Table(title="⚙️  當前配置", box=box.ROUNDED, border_style="green")
    table.add_column("設定項目", style="cyan")
    table.add_column("值", style="white")

    mode = "模擬交易 🟢" if config.exchange.paper_trading else "真實交易 🔴"
    table.add_row("交易模式", mode)
    table.add_row("交易所", f"{config.exchange.name} ({'測試網' if config.exchange.testnet else '正式網'})")
    table.add_row("交易對", ", ".join(config.trading.symbols))
    table.add_row("主要時間框架", config.trading.primary_timeframe)
    table.add_row("確認時間框架", ", ".join(config.trading.confirmation_timeframes))
    table.add_row("初始資金", f"{config.exchange.paper_balance:,.0f} USDT")
    table.add_row("每筆風險", f"{config.risk.risk_per_trade_pct}%")
    table.add_row("最低盈虧比", f"1:{config.risk.min_risk_reward}")
    table.add_row("止損倍數", f"{config.risk.atr_sl_multiplier}x ATR")
    table.add_row("止盈倍數", f"{config.risk.atr_tp_multiplier}x ATR")
    table.add_row("最大持倉數", str(config.risk.max_positions))
    table.add_row("每日損失限額", f"{config.risk.max_daily_loss_pct}%")
    table.add_row("最大回撤限額", f"{config.risk.max_drawdown_pct}%")
    table.add_row("移動止損", "開啟 ✅" if config.risk.trailing_stop else "關閉")
    ai_mode = f"Gemini ({config.agent.gemini_model})" if config.agent.gemini_api_key else "純規則模式 (無 API Key)"
    table.add_row("AI 引擎", ai_mode)
    table.add_row("最低信心門檻", f"{config.agent.min_confidence}%")
    table.add_row("掃描間隔", f"{config.trading.scan_interval} 秒")

    console.print(table)
    console.print("  ✅ 配置驗證通過\n")
    return True


async def run_bot():
    """啟動交易機器人"""
    if not check_config():
        console.print("[red]❌ 配置有誤，請檢查 .env 文件[/red]")
        sys.exit(1)

    if not config.exchange.paper_trading:
        console.print(
            Panel(
                "[bold red]⚠️  警告: 即將啟動真實資金交易！\n\n"
                "請確認:\n"
                "1. 你已充分測試過模擬模式\n"
                "2. 你了解量化交易的風險\n"
                "3. 你的止損設定合理\n\n"
                "5 秒後自動開始...[/bold red]",
                border_style="red",
            )
        )
        await asyncio.sleep(5)

    bot = OrchestratorAgent(cfg=config)
    await bot.run()


async def quick_test():
    """快速測試 - 執行單次週期並顯示結果"""
    console.print("\n[bold cyan]🧪 快速測試模式 (單次週期)[/bold cyan]\n")

    if not check_config():
        sys.exit(1)

    bot = OrchestratorAgent(cfg=config)
    bot._print_banner()

    console.print("\n[bold]執行單次分析週期...[/bold]\n")
    await bot.run_cycle()
    bot._print_status_table()

    await bot.exchange.close()
    console.print("\n✅ 測試完成")


def main():
    parser = argparse.ArgumentParser(
        description="AI 量化交易機器人",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用範例:
  python main.py               # 啟動交易機器人
  python main.py --check       # 只檢查配置
  python main.py --test        # 快速單次測試

注意: 預設為模擬交易模式，請在 .env 中配置 PAPER_TRADING=true
        """,
    )
    parser.add_argument("--check", action="store_true", help="只檢查配置，不啟動機器人")
    parser.add_argument("--test", action="store_true", help="執行單次週期測試")

    args = parser.parse_args()

    if args.check:
        check_config()
    elif args.test:
        asyncio.run(quick_test())
    else:
        asyncio.run(run_bot())


if __name__ == "__main__":
    main()
