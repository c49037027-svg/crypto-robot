"""
交易所客戶端封裝 (使用 ccxt)
支援: Binance / Bybit / OKX
紙上交易模式不會真實下單
"""
import asyncio
import ccxt
import ccxt.async_support as ccxt_async
from typing import Dict, List, Optional, Any
import pandas as pd
from utils.logger import get_logger

logger = get_logger("ExchangeClient")


class ExchangeClient:
    """
    統一交易所接口
    - fetch_ohlcv: 抓取 K 線資料
    - fetch_ticker: 當前價格
    - fetch_balance: 帳戶餘額
    - place_order: 下單
    - cancel_order: 取消訂單
    - fetch_open_orders: 查詢未完成訂單
    """

    SUPPORTED = {"binance", "bybit", "okx"}

    def __init__(self, name: str, api_key: str, api_secret: str,
                 testnet: bool = True, paper_trading: bool = True):
        self.name = name.lower()
        self.paper_trading = paper_trading
        self._exchange: Optional[ccxt_async.Exchange] = None

        if name.lower() not in self.SUPPORTED:
            logger.warning(f"交易所 {name} 可能未完整支援，使用預設設定")

        if not paper_trading:
            self._init_exchange(name, api_key, api_secret, testnet)

    def _init_exchange(self, name: str, api_key: str, api_secret: str, testnet: bool):
        cls = getattr(ccxt_async, name, None)
        if cls is None:
            raise ValueError(f"不支援的交易所: {name}")

        options = {}
        if testnet:
            if name == "binance":
                options = {"defaultType": "future"}

        self._exchange = cls({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": options,
        })

        if testnet:
            if hasattr(self._exchange, "set_sandbox_mode"):
                self._exchange.set_sandbox_mode(True)

        logger.info(f"已連接 {name} ({'測試網' if testnet else '正式網'})")

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                          limit: int = 300) -> pd.DataFrame:
        """抓取 K 線資料，返回 DataFrame"""
        try:
            if self._exchange:
                raw = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            else:
                # 模擬模式：使用公開 API (不需 key)
                raw = await self._fetch_public_ohlcv(symbol, timeframe, limit)

            if not raw:
                return pd.DataFrame()

            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            df = df.astype(float)
            return df

        except Exception as e:
            logger.error(f"抓取 {symbol} {timeframe} K 線失敗: {e}")
            return pd.DataFrame()

    async def _fetch_public_ohlcv(self, symbol: str, timeframe: str, limit: int) -> List:
        """使用 ccxt 公開接口抓資料 (無需 API key)"""
        try:
            ex = ccxt_async.binance({"enableRateLimit": True})
            raw = await ex.fetch_ohlcv(symbol, timeframe, limit=limit)
            await ex.close()
            return raw
        except Exception as e:
            logger.error(f"公開接口抓取失敗: {e}")
            return []

    async def fetch_ticker(self, symbol: str) -> Dict[str, float]:
        """取得當前盤口資料"""
        try:
            if self._exchange:
                t = await self._exchange.fetch_ticker(symbol)
            else:
                ex = ccxt_async.binance({"enableRateLimit": True})
                t = await ex.fetch_ticker(symbol)
                await ex.close()

            return {
                "symbol":    symbol,
                "last":      float(t.get("last", 0)),
                "bid":       float(t.get("bid", 0)),
                "ask":       float(t.get("ask", 0)),
                "volume":    float(t.get("baseVolume", 0)),
                "change_pct": float(t.get("percentage", 0)),
            }
        except Exception as e:
            logger.error(f"fetch_ticker {symbol} 失敗: {e}")
            return {"symbol": symbol, "last": 0, "bid": 0, "ask": 0, "volume": 0, "change_pct": 0}

    async def place_order(self, symbol: str, side: str, amount: float,
                          price: float = 0, order_type: str = "market") -> Dict:
        """
        下單 (紙上交易模式直接返回模擬成交)
        side: 'buy' | 'sell'
        order_type: 'market' | 'limit'
        """
        if self.paper_trading:
            logger.info(f"[模擬] {order_type.upper()} {side.upper()} {amount:.6f} {symbol} @ {price:.4f}")
            return {
                "id": f"paper_{symbol}_{side}_{int(asyncio.get_event_loop().time())}",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "type": order_type,
                "status": "filled",
                "filled": amount,
                "average": price,
            }

        try:
            if order_type == "market":
                order = await self._exchange.create_market_order(symbol, side, amount)
            else:
                order = await self._exchange.create_limit_order(symbol, side, amount, price)
            logger.info(f"[真實] 下單成功: {order['id']} {side} {amount} {symbol}")
            return order
        except Exception as e:
            logger.error(f"下單失敗: {e}")
            return {}

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self.paper_trading:
            return True
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"取消訂單失敗: {e}")
            return False

    async def fetch_balance(self) -> Dict[str, float]:
        """取得帳戶餘額"""
        if self.paper_trading:
            return {}  # 由 PaperPortfolio 管理
        try:
            bal = await self._exchange.fetch_balance()
            return {k: float(v["free"]) for k, v in bal["total"].items() if v and float(v) > 0}
        except Exception as e:
            logger.error(f"取得餘額失敗: {e}")
            return {}

    async def close(self):
        if self._exchange:
            await self._exchange.close()
