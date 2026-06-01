"""
市場分析代理人 (MarketAnalystAgent) V2.0
純規則技術指標引擎，不呼叫 Gemini。
"""
from typing import Dict
from utils.logger import get_logger

logger = get_logger("MarketAnalyst")


class MarketAnalystAgent:
    def __init__(self, gemini=None):  # gemini 參數保留以維持呼叫介面相容
        pass

    async def analyze(self, symbol: str, current_price: float,
                      primary_indicators: Dict[str, float],
                      multi_tf_data: Dict[str, Dict]) -> Dict:
        return self._rule_analyze(symbol, current_price, primary_indicators, multi_tf_data)

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
