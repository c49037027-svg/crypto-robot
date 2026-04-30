#!/usr/bin/env python3
"""
回測執行入口

用法:
  python backtest.py                          # BTC/USDT 過去 180 天
  python backtest.py --symbol ETH/USDT --days 365
  python backtest.py --symbol SOL/USDT --days 90 --confidence 70
  python backtest.py --all                    # 同時跑三個交易對
  python backtest.py --csv trades.csv         # 匯出交易記錄
"""
import argparse
import logging
import sys
from core.backtester import Backtester
from config import RiskConfig

# 回測模式靜默所有 log，只顯示報告
logging.disable(logging.CRITICAL)


def run_one(symbol: str, days: int, balance: float,
            confidence: float, max_age: int, csv_path: str = ""):
    print(f"\n{'='*40}")
    print(f"  抓取 {symbol} {days} 天資料...")
    try:
        df = Backtester.fetch_data(symbol, days)
    except Exception as e:
        print(f"  ❌ 資料抓取失敗: {e}")
        return

    bt = Backtester(
        symbol=symbol,
        initial_balance=balance,
        min_confidence=confidence,
        max_position_age_bars=max_age,
    )

    print(f"  開始回測 (信心門檻 {confidence:.0f}%)...\n")
    bt.run(df)
    print(bt.report())
    print(bt.debug_losers(n_each=3))

    if csv_path:
        bt.export_csv(csv_path)
        print(f"\n  交易記錄已存至: {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="量化策略回測")
    parser.add_argument("--symbol",     default="BTC/USDT",
                        help="交易對 (預設 BTC/USDT)")
    parser.add_argument("--days",       type=int,   default=180,
                        help="回測天數 (預設 180)")
    parser.add_argument("--balance",    type=float, default=10_000.0,
                        help="初始資金 USDT (預設 10000)")
    parser.add_argument("--confidence", type=float, default=65.0,
                        help="最低信心分數門檻 (預設 65)")
    parser.add_argument("--max-age",    type=int,   default=168,
                        help="最大持倉 K 線數 (預設 168)")
    parser.add_argument("--all",        action="store_true",
                        help="同時回測 BTC/ETH/SOL")
    parser.add_argument("--csv",        default="",
                        help="匯出交易記錄 CSV 路徑")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.all else [args.symbol]

    for sym in symbols:
        csv_path = ""
        if args.csv and len(symbols) == 1:
            csv_path = args.csv
        elif args.csv:
            csv_path = args.csv.replace(".csv", f"_{sym.replace('/','_')}.csv")

        run_one(sym, args.days, args.balance, args.confidence, args.max_age, csv_path)

    print()


if __name__ == "__main__":
    main()
