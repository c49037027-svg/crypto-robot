"""
市場數據管理器
負責快取和提供多時間框架 OHLCV 數據
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
import pandas as pd
from core.exchange_client import ExchangeClient
from core.indicators import compute_indicators, get_latest
from utils.logger import get_logger

logger = get_logger("DataManager")


class DataManager:
    def __init__(self, exchange: ExchangeClient,
                 primary_tf: str = "1h",
                 confirm_tfs: List[str] = None,
                 ohlcv_limit: int = 300):
        self.exchange = exchange
        self.primary_tf = primary_tf
        self.confirm_tfs = confirm_tfs or ["4h", "1d"]
        self.all_tfs = [primary_tf] + [tf for tf in self.confirm_tfs if tf != primary_tf]
        self.ohlcv_limit = ohlcv_limit

        # 快取結構: {symbol: {timeframe: DataFrame}}
        self._cache: Dict[str, Dict[str, pd.DataFrame]] = {}
        self._indicator_cache: Dict[str, Dict[str, Dict]] = {}
        self._last_update: Dict[str, Dict[str, datetime]] = {}
        # 即時價格快取（由快速更新迴圈寫入）
        self._live_prices: Dict[str, float] = {}

    async def refresh(self, symbol: str) -> bool:
        """刷新指定交易對的所有時間框架數據"""
        success = True
        tasks = [self._fetch_tf(symbol, tf) for tf in self.all_tfs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        if symbol not in self._cache:
            self._cache[symbol] = {}
            self._indicator_cache[symbol] = {}
            self._last_update[symbol] = {}

        for tf, result in zip(self.all_tfs, results):
            if isinstance(result, Exception):
                logger.error(f"刷新 {symbol}/{tf} 失敗: {result}")
                success = False
                continue
            if result is not None and not result.empty:
                self._cache[symbol][tf] = result
                indicators = compute_indicators(result)
                self._indicator_cache[symbol][tf] = get_latest(indicators)
                self._last_update[symbol][tf] = datetime.now(timezone.utc)

        return success

    async def _fetch_tf(self, symbol: str, tf: str) -> Optional[pd.DataFrame]:
        df = await self.exchange.fetch_ohlcv(symbol, tf, self.ohlcv_limit)
        return df if not df.empty else None

    def get_ohlcv(self, symbol: str, timeframe: str = None) -> Optional[pd.DataFrame]:
        tf = timeframe or self.primary_tf
        return self._cache.get(symbol, {}).get(tf)

    def get_indicators(self, symbol: str, timeframe: str = None) -> Optional[Dict[str, float]]:
        tf = timeframe or self.primary_tf
        return self._indicator_cache.get(symbol, {}).get(tf)

    def update_live_price(self, symbol: str, price: float):
        """由快速更新迴圈寫入即時價格"""
        if price > 0:
            self._live_prices[symbol] = price

    def get_current_price(self, symbol: str) -> float:
        # 優先用即時價格（每 10 秒更新），回退用 OHLCV 快取
        if symbol in self._live_prices:
            return self._live_prices[symbol]
        df = self.get_ohlcv(symbol)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
        return 0.0

    def get_atr(self, symbol: str, timeframe: str = None) -> float:
        ind = self.get_indicators(symbol, timeframe)
        if ind:
            return ind.get("atr", 0.0)
        return 0.0

    def get_multi_tf_summary(self, symbol: str) -> Dict[str, Dict]:
        """返回所有時間框架的指標摘要"""
        summary = {}
        for tf in self.all_tfs:
            ind = self._indicator_cache.get(symbol, {}).get(tf)
            if ind:
                summary[tf] = ind
        return summary

    def is_ready(self, symbol: str) -> bool:
        """確認該交易對的主要時間框架數據已就緒"""
        df = self.get_ohlcv(symbol, self.primary_tf)
        return df is not None and len(df) >= 50
