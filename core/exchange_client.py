"""
交易所客戶端封裝 (使用 ccxt)
支援: Binance / Bybit / OKX
紙上交易模式不會真實下單
"""
import asyncio
import time as _time
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
                 passphrase: str = "", testnet: bool = True, paper_trading: bool = True):
        self.name = name.lower()
        self.paper_trading = paper_trading
        self._exchange: Optional[ccxt_async.Exchange] = None
        # 虛擬條件單薄（paper trading 用，key = order_id）
        self._virtual_orders: Dict[str, Dict] = {}

        if name.lower() not in self.SUPPORTED:
            logger.warning(f"交易所 {name} 可能未完整支援，使用預設設定")

        # OKX 模擬模式：即使 paper_trading=true 也建立連線以取得真實行情
        if not paper_trading or (name.lower() == "okx" and api_key):
            self._init_exchange(name, api_key, api_secret, passphrase, testnet)

    def _init_exchange(self, name: str, api_key: str, api_secret: str,
                       passphrase: str, testnet: bool):
        cls = getattr(ccxt_async, name, None)
        if cls is None:
            raise ValueError(f"不支援的交易所: {name}")

        config: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }
        if passphrase:
            config["password"] = passphrase

        if name == "okx":
            config["options"] = {"defaultType": "swap"}

        self._exchange = cls(config)

        if testnet and hasattr(self._exchange, "set_sandbox_mode"):
            self._exchange.set_sandbox_mode(True)

        logger.info(f"已連接 {name} ({'模擬帳戶' if testnet else '正式帳戶'})")

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

    async def place_stop_order(self, symbol: str, close_side: str,
                               qty: float, stop_price: float, label: str = "") -> str:
        """
        掛條件止損/止盈單
        close_side: 平多=sell, 平空=buy
        label: sl | tp1 | tp2
        返回 order_id（失敗返回空字串）

        觸發方向:
          SL  sell (平多): 價格 <= stop  → trigger_dir="down"
          TP  sell (平多): 價格 >= stop  → trigger_dir="up"
          SL  buy  (平空): 價格 >= stop  → trigger_dir="up"
          TP  buy  (平空): 價格 <= stop  → trigger_dir="down"
        """
        order_id = f"stop_{symbol.replace('/','_')}_{label}_{int(_time.time()*1000)}"

        is_tp = label.startswith("tp")
        if close_side == "sell":
            trigger_dir = "up" if is_tp else "down"
        else:
            trigger_dir = "down" if is_tp else "up"

        if self.paper_trading:
            self._virtual_orders[order_id] = {
                "symbol":      symbol,
                "side":        close_side,
                "qty":         qty,
                "stop_price":  stop_price,
                "label":       label,
                "trigger_dir": trigger_dir,
                "status":      "open",
                "fill_price":  0.0,
            }
            logger.info(
                f"[虛擬條件單+] {label.upper():3} {symbol} {close_side} "
                f"qty={qty:.4f} @ {stop_price:.4f}"
            )
            return order_id

        # 真實交易所
        try:
            close_side_real = close_side
            if self.name == "okx":
                params = {"algoOrdType": "conditional",
                          "triggerPx": str(stop_price), "ordPx": "-1", "tdMode": "cash"}
                order = await self._exchange.create_order(
                    symbol, "market", close_side_real, qty, params=params)
            else:
                order = await self._exchange.create_order(
                    symbol, "stop_market", close_side_real, qty,
                    params={"stopPrice": stop_price})
            oid = order.get("id", order_id)
            logger.info(f"[條件單+] {label.upper()} {symbol} @ {stop_price:.4f} | ID: {oid}")
            return oid
        except Exception as e:
            logger.error(f"place_stop_order {symbol} {label}: {e}")
            return ""

    async def cancel_stop_order(self, order_id: str, symbol: str) -> bool:
        """取消條件單"""
        if not order_id:
            return True
        if self.paper_trading:
            if order_id in self._virtual_orders:
                self._virtual_orders[order_id]["status"] = "cancelled"
                logger.debug(f"[虛擬條件單-] 已取消 {order_id.split('_')[-1]}")
            return True
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.debug(f"cancel_stop_order {order_id}: {e}")
            return False

    def check_virtual_stops(self, symbol: str, current_price: float) -> List[Dict]:
        """
        掃描虛擬條件單，回傳本次觸發的列表（paper trading 用）
        返回: [{id, label, qty, stop_price, fill_price}]
        trigger_dir="down": price <= stop_price 觸發（止損做多 / 止盈做空）
        trigger_dir="up":   price >= stop_price 觸發（止盈做多 / 止損做空）
        """
        triggered = []
        for oid, order in list(self._virtual_orders.items()):
            if order["symbol"] != symbol or order["status"] != "open":
                continue
            stop = order["stop_price"]
            tdir = order.get("trigger_dir", "down")
            hit = (tdir == "down" and current_price <= stop) or \
                  (tdir == "up"   and current_price >= stop)
            if hit:
                order["status"]     = "filled"
                order["fill_price"] = current_price
                triggered.append({
                    "id":          oid,
                    "label":       order["label"],
                    "qty":         order["qty"],
                    "stop_price":  stop,
                    "fill_price":  current_price,
                })
                logger.info(
                    f"[虛擬條件單✓] {order['label'].upper()} {symbol} "
                    f"觸發 @ {current_price:.4f} (設定 {stop:.4f})"
                )
        return triggered

    async def fetch_funding_rate(self, symbol: str) -> float:
        """
        取得永續合約資費率，返回年化百分比 (%)
        正值: 多頭付費，負值: 空頭付費
        無法取得時返回 0.0
        """
        try:
            ex = ccxt_async.binance({
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            })
            fr = await ex.fetch_funding_rate(symbol)
            await ex.close()
            rate = float(fr.get("fundingRate", 0))
            # Binance 每 8 小時結算，1 天 3 次，年化 = rate * 3 * 365
            return rate * 3 * 365 * 100
        except Exception as e:
            logger.debug(f"fetch_funding_rate {symbol}: {e}")
            return 0.0

    async def close(self):
        if self._exchange:
            await self._exchange.close()
