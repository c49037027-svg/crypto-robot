"""
市場分析代理人 (MarketAnalystAgent)
- 有 Gemini Key → AI 深度分析
- 無 Key       → 純規則分析 (自動回退)
"""
import asyncio
from typing import Dict
from agents.gemini_client import GeminiClient
from utils.logger import get_logger

logger = get_logger("MarketAnalyst")

_SYSTEM = """你是一位頂尖的加密貨幣量化市場分析師。
根據技術指標數據，輸出嚴格 JSON 格式的市場分析，不要包含任何說明文字。

必須輸出以下 JSON:
{
  "trend": "bullish" | "bearish" | "sideways",
  "trend_strength": 0到100的整數,
  "momentum": "strong_up" | "up" | "neutral" | "down" | "strong_down",
  "volatility": "high" | "normal" | "low",
  "volume_confirms": true或false,
  "multi_tf_alignment": true或false,
  "regime": "trending" | "ranging" | "volatile",
  "key_levels": { "support": 數字, "resistance": 數字 },
  "analysis": "50字以內的中文分析摘要",
  "risk_level": "low" | "medium" | "high"
}

regime 判定規則:
- trending: ADX > 25，價格明顯偏離 BB 中軌，EMA 順向排列
- ranging:  ADX < 20，價格在 BB 中軌附近震盪，EMA 糾纏
- volatile: ATR > 3% 且方向不明確

multi_tf_alignment 嚴格規則（兩個條件必須同時成立）:
- 多頭: 4H EMA21 > 4H EMA55  且  1D 收盤 > 1D EMA55
- 空頭: 4H EMA21 < 4H EMA55  且  1D 收盤 < 1D EMA55
- 若任一條件不符，輸出 false"""


class MarketAnalystAgent:
    def __init__(self, gemini: GeminiClient = None):
        self.gemini = gemini or GeminiClient("")  # 無 key → 純規則

    async def analyze(self, symbol: str, current_price: float,
                      primary_indicators: Dict[str, float],
                      multi_tf_data: Dict[str, Dict]) -> Dict:

        # ── 先嘗試 Gemini AI 分析 ──
        if self.gemini.enabled:
            result = await self._ai_analyze(symbol, current_price,
                                            primary_indicators, multi_tf_data)
            if result:
                result["symbol"] = symbol
                result["price"]  = current_price
                logger.info(
                    f"[Gemini分析] {symbol} | {result.get('trend')} "
                    f"({result.get('trend_strength')}%) | 風險:{result.get('risk_level')}"
                )
                return result

        # ── 回退: 純規則分析 ──
        return self._rule_analyze(symbol, current_price, primary_indicators, multi_tf_data)

    async def _ai_analyze(self, symbol, price, ind, multi_tf_data) -> Dict:
        """Gemini AI 分析"""
        ema9  = ind.get("ema9",  price)
        ema21 = ind.get("ema21", price)
        ema200= ind.get("ema200",price)
        rsi   = ind.get("rsi",  50)
        macd  = ind.get("macd",  0)
        msig  = ind.get("macd_signal", 0)
        bb_u  = ind.get("bb_upper", price*1.02)
        bb_m  = ind.get("bb_mid",   price)
        bb_l  = ind.get("bb_lower", price*0.98)
        atr   = ind.get("atr", price*0.01)
        stoch = ind.get("stoch_rsi", 50)
        vol   = ind.get("volume", 0)
        vsma  = ind.get("vol_sma20", 1)

        tf_lines = []
        for tf, ti in multi_tf_data.items():
            if ti:
                tf_lines.append(
                    f"  [{tf}] EMA9={'>' if ti.get('ema9',price)>ti.get('ema21',price) else '<'}EMA21  RSI={ti.get('rsi',50):.0f}"
                )

        prompt = f"""交易對: {symbol}  當前價: {price:.4f} USDT

EMA9={ema9:.4f} EMA21={ema21:.4f} EMA200={ema200:.4f}
RSI={rsi:.1f}  StochRSI={stoch:.1f}
MACD={macd:.6f} vs Signal={msig:.6f} ({'金叉' if macd>msig else '死叉'})
BB 上={bb_u:.4f} 中={bb_m:.4f} 下={bb_l:.4f}
ATR={atr:.4f} ({atr/price*100:.2f}%)
成交量倍率={vol/(vsma or 1):.2f}x

多時框:
{chr(10).join(tf_lines) or '  (無資料)'}

請嚴格輸出 JSON，不含其他文字。"""

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, self.gemini.call, _SYSTEM, prompt)
        return self.gemini.parse_json(raw)

    @staticmethod
    def detect_regime(ind: Dict, price: float) -> str:
        """
        市場狀態過濾器 (Market Regime Filter)
        trending : ADX > 25，EMA 順向排列
        ranging  : ADX < 20，BB 帶窄，EMA 糾纏
        volatile : ATR > 3% 且趨勢不明
        """
        adx_val  = ind.get("adx", 20)
        atr_val  = ind.get("atr", price * 0.02)
        atr_pct  = atr_val / price * 100 if price > 0 else 2
        bb_u     = ind.get("bb_upper", price * 1.02)
        bb_l     = ind.get("bb_lower", price * 0.98)
        bb_width = (bb_u - bb_l) / price * 100 if price > 0 else 2
        ema9     = ind.get("ema9",  price)
        ema21    = ind.get("ema21", price)
        ema55    = ind.get("ema55", price)

        ema_aligned = (ema9 > ema21 > ema55) or (ema9 < ema21 < ema55)

        if atr_pct > 4 and not ema_aligned:
            return "volatile"
        if adx_val > 25 and ema_aligned:
            return "trending"
        if adx_val < 20 and bb_width < 4:
            return "ranging"
        # 預設：偏趨勢（在趨勢與震盪之間）
        return "trending" if adx_val >= 20 else "ranging"

    def _rule_analyze(self, symbol, price, ind, multi_tf_data) -> Dict:
        """純規則分析"""
        ema9  = ind.get("ema9",  price)
        ema21 = ind.get("ema21", price)
        ema55 = ind.get("ema55", price)
        ema200= ind.get("ema200",price)
        rsi   = ind.get("rsi",  50)
        macd  = ind.get("macd",  0)
        msig  = ind.get("macd_signal", 0)
        bb_u  = ind.get("bb_upper", price*1.02)
        bb_m  = ind.get("bb_mid",   price)
        bb_l  = ind.get("bb_lower", price*0.98)
        atr   = ind.get("atr", price*0.01)
        vol   = ind.get("volume", 0)
        vsma  = ind.get("vol_sma20", 1)
        stoch = ind.get("stoch_rsi", 50)

        bull = sum([price>ema200, ema9>ema21, ema21>ema55, ema9>ema55])
        bear = sum([price<ema200, ema9<ema21, ema21<ema55, ema9<ema55])

        if bull >= 3:   trend = "bullish"
        elif bear >= 3: trend = "bearish"
        else:           trend = "sideways"

        trend_strength = int(max(bull, bear) / 4 * 100)

        if rsi > 60 and macd > msig:   momentum = "strong_up"
        elif rsi > 50 and macd > msig: momentum = "up"
        elif rsi < 40 and macd < msig: momentum = "strong_down"
        elif rsi < 50 and macd < msig: momentum = "down"
        else:                          momentum = "neutral"

        atr_pct = atr / price * 100
        if atr_pct > 3:   volatility = "high"
        elif atr_pct < 1: volatility = "low"
        else:             volatility = "normal"

        vol_ratio = vol / vsma if vsma > 0 else 1.0
        volume_confirms = vol_ratio > 1.1

        # 嚴格多時框確認:
        # 多頭需要: 4H EMA21 > 4H EMA55  且  1D 收盤 > 1D EMA55
        # 空頭需要: 4H EMA21 < 4H EMA55  且  1D 收盤 < 1D EMA55
        tf4h = multi_tf_data.get("4h", {})
        tf1d = multi_tf_data.get("1d", {})
        tf4h_ema21 = tf4h.get("ema21", price)
        tf4h_ema55 = tf4h.get("ema55", price)
        tf1d_close = tf1d.get("close", price)
        tf1d_ema55 = tf1d.get("ema55", price)
        mtf_bull_ok = (tf4h_ema21 > tf4h_ema55) and (tf1d_close > tf1d_ema55)
        mtf_bear_ok = (tf4h_ema21 < tf4h_ema55) and (tf1d_close < tf1d_ema55)
        multi_tf_alignment = (trend == "bullish" and mtf_bull_ok) or \
                             (trend == "bearish" and mtf_bear_ok)

        support    = bb_l if trend != "bullish" else max(bb_l, ema200)
        resistance = bb_u if trend != "bearish" else min(bb_u, ema200)

        risk = sum([volatility=="high", rsi>75 or rsi<25, not volume_confirms,
                    not multi_tf_alignment, trend=="sideways"])
        risk_level = "high" if risk>=4 else ("medium" if risk>=2 else "low")

        regime = self.detect_regime(ind, price)

        result = {
            "symbol": symbol, "price": price,
            "trend": trend, "trend_strength": trend_strength,
            "momentum": momentum, "volatility": volatility,
            "volume_confirms": volume_confirms,
            "multi_tf_alignment": multi_tf_alignment,
            "regime": regime,
            "key_levels": {"support": round(support,4), "resistance": round(resistance,4)},
            "analysis": f"{trend.upper()} | RSI={rsi:.0f} | {'金叉' if macd>msig else '死叉'} | {regime} | 規則模式",
            "risk_level": risk_level,
            "_bull_signals": bull, "_bear_signals": bear,
            "_vol_ratio": round(vol_ratio,2), "_atr_pct": round(atr_pct,3),
            "_mtf_bull_ok": mtf_bull_ok, "_mtf_bear_ok": mtf_bear_ok,
        }
        logger.info(
            f"[規則分析] {symbol} | {trend.upper()} ({trend_strength}%) | "
            f"Regime:{regime} | 風險:{risk_level} | "
            f"4H EMA21{'>' if mtf_bull_ok else '<'}EMA55 | "
            f"1D {'>' if tf1d_close > tf1d_ema55 else '<'}EMA55 | "
            f"MTF:{'✅' if multi_tf_alignment else '❌'}"
        )
        return result
