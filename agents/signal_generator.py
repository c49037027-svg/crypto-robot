"""
信號生成代理人 (SignalGeneratorAgent)
- 有 Gemini Key → AI 生成信號 + 信心分數
- 無 Key       → 多指標評分系統 (自動回退)
"""
import asyncio
from typing import Dict
from agents.gemini_client import GeminiClient
from utils.logger import get_logger

logger = get_logger("SignalGenerator")

_SYSTEM = """你是一位專業的加密貨幣量化交易信號系統。

信號規則:
- LONG:  上升趨勢 + EMA多頭 + RSI 42-68 + MACD金叉 + 成交量放大
- SHORT: 下降趨勢 + EMA空頭 + RSI 32-58 + MACD死叉 + 成交量放大
- HOLD:  條件不明確 / 超買超賣 / 橫盤

輸出嚴格 JSON，不含其他文字:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": 0到100的整數,
  "entry_type": "market",
  "entry_price_suggestion": 0,
  "reasoning": "進場理由(50字以內中文)",
  "invalidation": "信號失效條件(30字以內)",
  "signal_quality": "A" | "B" | "C"
}"""


class SignalGeneratorAgent:
    def __init__(self, gemini: GeminiClient = None):
        self.gemini = gemini or GeminiClient("")

    async def generate(self, symbol: str, market_analysis: Dict,
                       indicators: Dict[str, float],
                       current_price: float) -> Dict:

        # ── 先嘗試 Gemini AI ──
        if self.gemini.enabled:
            result = await self._ai_generate(symbol, market_analysis,
                                             indicators, current_price)
            if result and result.get("signal") in ("LONG", "SHORT", "HOLD"):
                result["symbol"] = symbol
                result["price"]  = current_price
                sig = result["signal"]
                conf = result.get("confidence", 0)
                if sig != "HOLD":
                    logger.info(
                        f"[Gemini信號] {symbol} {sig} | 信心:{conf}% | "
                        f"品質:{result.get('signal_quality')} | {result.get('reasoning','')}"
                    )
                else:
                    logger.debug(f"[Gemini信號] {symbol} HOLD")
                return result

        # ── 回退: 純規則評分 ──
        return self._rule_generate(symbol, market_analysis, indicators, current_price)

    async def _ai_generate(self, symbol, analysis, ind, price) -> Dict:
        ema9  = ind.get("ema9",  price)
        ema21 = ind.get("ema21", price)
        ema200= ind.get("ema200",price)
        rsi   = ind.get("rsi",  50)
        macd  = ind.get("macd",  0)
        msig  = ind.get("macd_signal", 0)
        bb_u  = ind.get("bb_upper", price*1.02)
        bb_l  = ind.get("bb_lower", price*0.98)
        atr   = ind.get("atr", price*0.01)
        stoch = ind.get("stoch_rsi", 50)
        vol   = ind.get("volume", 0)
        vsma  = ind.get("vol_sma20", 1)

        prompt = f"""交易對: {symbol}  價格: {price:.4f} USDT

[市場分析]
趨勢: {analysis.get('trend')} (強度:{analysis.get('trend_strength')}%)
動量: {analysis.get('momentum')}  波動率: {analysis.get('volatility')}
成交量確認: {analysis.get('volume_confirms')}  多時框共振: {analysis.get('multi_tf_alignment')}
風險等級: {analysis.get('risk_level')}
摘要: {analysis.get('analysis')}

[指標]
EMA9={ema9:.4f} EMA21={ema21:.4f} EMA200={ema200:.4f}
RSI={rsi:.1f}  StochRSI={stoch:.1f}
MACD={'金叉' if macd>msig else '死叉'} ({macd:.6f} vs {msig:.6f})
BB 上={bb_u:.4f} 下={bb_l:.4f}  ATR={atr:.4f}
成交量={vol/(vsma or 1):.2f}x均量

支撐: {analysis.get('key_levels',{}).get('support',0):.4f}
阻力: {analysis.get('key_levels',{}).get('resistance',0):.4f}

請輸出 JSON 信號。"""

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, self.gemini.call, _SYSTEM, prompt)
        return self.gemini.parse_json(raw)

    def _rule_generate(self, symbol, analysis, ind, price) -> Dict:
        """多指標評分系統 (純規則)"""
        ema9  = ind.get("ema9",  price)
        ema21 = ind.get("ema21", price)
        ema55 = ind.get("ema55", price)
        ema200= ind.get("ema200",price)
        rsi   = ind.get("rsi",  50)
        macd  = ind.get("macd",  0)
        msig  = ind.get("macd_signal", 0)
        mhist = ind.get("macd_hist", 0)
        bb_u  = ind.get("bb_upper", price*1.02)
        bb_m  = ind.get("bb_mid",   price)
        stoch = ind.get("stoch_rsi", 50)
        vol   = ind.get("volume", 0)
        vsma  = ind.get("vol_sma20", 1)
        vol_r = vol / vsma if vsma > 0 else 1.0
        trend = analysis.get("trend", "sideways")
        mtf   = analysis.get("multi_tf_alignment", False)

        # ─ 多頭評分 ─
        lp, lr = 0, []
        if price > ema200: lp+=15; lr.append("價格>EMA200")
        if ema9 > ema21:   lp+=12; lr.append("EMA金叉")
        if ema21 > ema55:  lp+=8;  lr.append("EMA21>EMA55")
        if price > bb_m:   lp+=6;  lr.append("價格>BB中軌")
        if price < bb_u:   lp+=4;  lr.append("未超買")
        if 42<=rsi<=68:    lp+=12; lr.append(f"RSI健康({rsi:.0f})")
        elif 35<=rsi<42 or 68<rsi<=72: lp+=6
        if macd > msig:    lp+=10; lr.append("MACD金叉")
        if mhist > 0:      lp+=4;  lr.append("MACD柱正")
        if stoch < 70:     lp+=4;  lr.append("StochRSI<70")
        if vol_r > 1.3:    lp+=15; lr.append(f"量放大{vol_r:.1f}x")
        elif vol_r > 1.1:  lp+=8
        elif vol_r > 0.9:  lp+=4
        if mtf and trend=="bullish": lp+=10; lr.append("多時框共振")

        # ─ 空頭評分 ─
        sp, sr = 0, []
        if price < ema200: sp+=15; sr.append("價格<EMA200")
        if ema9 < ema21:   sp+=12; sr.append("EMA死叉")
        if ema21 < ema55:  sp+=8;  sr.append("EMA21<EMA55")
        if price < bb_m:   sp+=6;  sr.append("價格<BB中軌")
        if 32<=rsi<=58:    sp+=12; sr.append(f"RSI弱勢({rsi:.0f})")
        elif 28<=rsi<32 or 58<rsi<=65: sp+=6
        if macd < msig:    sp+=10; sr.append("MACD死叉")
        if mhist < 0:      sp+=4;  sr.append("MACD柱負")
        if stoch > 30:     sp+=4
        if vol_r > 1.3:    sp+=15; sr.append(f"量放大{vol_r:.1f}x")
        elif vol_r > 1.1:  sp+=8
        elif vol_r > 0.9:  sp+=4
        if mtf and trend=="bearish": sp+=10; sr.append("多時框共振")

        MAX = 100
        lc = min(100, int(lp/MAX*100))
        sc = min(100, int(sp/MAX*100))

        if abs(lp-sp) < 15:
            sig, conf, reason, inv = "HOLD", 40, "多空分歧不足", ""
            quality = "C"
        elif lp > sp and lc >= 50:
            sig = "LONG";  conf = lc
            reason = " + ".join(lr[:4])
            inv = f"EMA死叉或跌破EMA200({ema200:.2f})"
            quality = "A" if lc>=80 else ("B" if lc>=65 else "C")
        elif sp > lp and sc >= 50:
            sig = "SHORT"; conf = sc
            reason = " + ".join(sr[:4])
            inv = f"EMA金叉或突破EMA200({ema200:.2f})"
            quality = "A" if sc>=80 else ("B" if sc>=65 else "C")
        else:
            sig, conf, reason, inv = "HOLD", max(lc,sc), "信心不足", ""
            quality = "C"

        if sig != "HOLD":
            logger.info(f"[規則信號] {symbol} {sig} | 信心:{conf}% | 品質:{quality} | {reason}")
        else:
            logger.debug(f"[規則信號] {symbol} HOLD | 多:{lc}% 空:{sc}%")

        return {
            "symbol": symbol, "price": price,
            "signal": sig, "confidence": conf,
            "entry_type": "market", "entry_price_suggestion": 0,
            "reasoning": reason, "invalidation": inv,
            "signal_quality": quality,
            "_long_pts": lp, "_short_pts": sp,
        }
