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

_SYSTEM = """你是一位專業的加密貨幣量化交易信號系統，負責最終的進場決策。

你的職責是輸出一個「最終信心分數」，這個數字必須已經在內部分析中充分考量所有風險因素。

【信號邏輯】
- LONG:  趨勢向上 + EMA多頭排列 + RSI健康 + MACD金叉 + 成交量確認
- SHORT: 趨勢向下 + EMA空頭排列 + RSI弱勢 + MACD死叉 + 成交量確認
- HOLD:  條件矛盾 / 市場狀態不明 / 風險過高

【信心分數內部扣分規則（你必須自行執行，不需列出）】
- 成交量低於 20MA：自動大幅扣分（-15至-25分）
- 價格過度接近強支撐/壓力位（距離 < 0.5%）：扣分（-10至-20分）
- ADX < 20（震盪市做趨勢單）：扣分（-10分）
- RSI 超買（>75）做多 或 超賣（<25）做空：扣分（-15分）
- 多時框方向不一致：扣分（-15分）

【市場狀態調整】
- Regime=ranging（震盪市）：提高 RSI/BB 判斷比重，降低 EMA 趨勢比重
- Regime=trending（趨勢市）：提高 EMA/MACD/多時框比重，RSI 鈍化可忽略

最終輸出的 confidence 必須是已扣分後的綜合決策分數。

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
市場狀態(Regime): {analysis.get('regime', 'trending')}
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
        """多指標評分系統 (純規則) — 動態權重依 Regime 調整"""
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
        bb_l  = ind.get("bb_lower", price*0.98)
        stoch = ind.get("stoch_rsi", 50)
        vol   = ind.get("volume", 0)
        vsma  = ind.get("vol_sma20", 1)
        vol_r = vol / vsma if vsma > 0 else 1.0
        adx_v = ind.get("adx", 25)
        trend  = analysis.get("trend", "sideways")
        mtf    = analysis.get("multi_tf_alignment", False)
        regime = analysis.get("regime", "trending")

        # ── 硬性前置過濾（不達標直接 HOLD，跳過評分）──

        # 1. 成交量硬門檻: < 0.7x 均量 = 流動性枯竭
        _hold = {"symbol": symbol, "price": price, "signal": "HOLD", "confidence": 30,
                 "entry_type": "market", "entry_price_suggestion": 0,
                 "reasoning": "", "invalidation": "", "signal_quality": "C",
                 "_long_pts": 0, "_short_pts": 0}
        if vol_r < 0.7:
            logger.debug(f"[量能過濾] {symbol} {vol_r:.1f}x < 0.7x，HOLD")
            return {**_hold, "reasoning": f"量能枯竭({vol_r:.1f}x)"}

        # ── Regime 動態權重 ──
        # trending: EMA/MACD/多時框加重，RSI/BB 降低（趨勢中容易鈍化）
        # ranging:  RSI/BB 加重，EMA 降低（避免假突破雙巴）
        if regime == "trending":
            w_ema, w_rsi, w_bb, w_mtf, w_vol = 1.4, 0.7, 0.7, 1.5, 1.2
        elif regime == "ranging":
            w_ema, w_rsi, w_bb, w_mtf, w_vol = 0.7, 1.5, 1.5, 0.7, 1.0
        else:  # volatile — 提高門檻，整體縮減
            w_ema, w_rsi, w_bb, w_mtf, w_vol = 0.8, 0.8, 0.8, 0.8, 1.0

        def w(base, weight): return base * weight

        # ─ 多頭評分 ─
        lp, lr = 0.0, []
        if price > ema200: lp+=w(15,w_ema); lr.append("價格>EMA200")
        if ema9 > ema21:   lp+=w(12,w_ema); lr.append("EMA金叉")
        if ema21 > ema55:  lp+=w(8, w_ema); lr.append("EMA21>EMA55")
        if price > bb_m:   lp+=w(6, w_bb);  lr.append("價格>BB中軌")
        if price < bb_u:   lp+=w(4, w_bb);  lr.append("未超買")
        if 42<=rsi<=68:    lp+=w(12,w_rsi); lr.append(f"RSI健康({rsi:.0f})")
        elif 35<=rsi<42 or 68<rsi<=72: lp+=w(6,w_rsi)
        if macd > msig:    lp+=w(10,w_ema); lr.append("MACD金叉")
        if mhist > 0:      lp+=w(4, w_ema); lr.append("MACD柱正")
        if stoch < 70:     lp+=4
        if vol_r > 1.3:    lp+=w(15,w_vol); lr.append(f"量放大{vol_r:.1f}x")
        elif vol_r > 1.1:  lp+=w(8, w_vol)
        elif vol_r > 0.9:  lp+=w(4, w_vol)
        if mtf and trend=="bullish": lp+=w(10,w_mtf); lr.append("多時框共振")

        # ─ 空頭評分 ─
        sp, sr = 0.0, []
        if price < ema200: sp+=w(15,w_ema); sr.append("價格<EMA200")
        if ema9 < ema21:   sp+=w(12,w_ema); sr.append("EMA死叉")
        if ema21 < ema55:  sp+=w(8, w_ema); sr.append("EMA21<EMA55")
        if price < bb_m:   sp+=w(6, w_bb);  sr.append("價格<BB中軌")
        if 32<=rsi<=58:    sp+=w(12,w_rsi); sr.append(f"RSI弱勢({rsi:.0f})")
        elif 28<=rsi<32 or 58<rsi<=65: sp+=w(6,w_rsi)
        if macd < msig:    sp+=w(10,w_ema); sr.append("MACD死叉")
        if mhist < 0:      sp+=w(4, w_ema); sr.append("MACD柱負")
        if stoch > 30:     sp+=4
        if vol_r > 1.3:    sp+=w(15,w_vol); sr.append(f"量放大{vol_r:.1f}x")
        elif vol_r > 1.1:  sp+=w(8, w_vol)
        elif vol_r > 0.9:  sp+=w(4, w_vol)
        if mtf and trend=="bearish": sp+=w(10,w_mtf); sr.append("多時框共振")

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

        # ── ADX < 20: 震盪市方向性過濾 ──
        # 震盪市只接受「逆勢回歸」型進場：
        #   LONG  需在 BB 下半段（低位買支撐）
        #   SHORT 需在 BB 上半段（高位賣壓力）
        if adx_v < 20 and sig != "HOLD":
            bb_range = (bb_u - bb_l) if bb_u > bb_l else 1
            bb_pct = (price - bb_l) / bb_range
            if sig == "LONG" and bb_pct > 0.5:
                logger.debug(f"[ADX過濾] {symbol} ADX={adx_v:.0f}<20 LONG@BB{bb_pct*100:.0f}% 禁止高位順勢多")
                sig, conf, reason, inv = "HOLD", 40, f"震盪市頂部禁多(ADX={adx_v:.0f})", ""
                quality = "C"
            elif sig == "SHORT" and bb_pct <= 0.5:
                logger.debug(f"[ADX過濾] {symbol} ADX={adx_v:.0f}<20 SHORT@BB{bb_pct*100:.0f}% 禁止低位順勢空")
                sig, conf, reason, inv = "HOLD", 40, f"震盪市底部禁空(ADX={adx_v:.0f})", ""
                quality = "C"

        # 3. BB > 上軌: 強勁突破中禁止開空（等回落再空）
        if sig == "SHORT" and price > bb_u:
            logger.info(f"[BB過濾] {symbol} 價格>{bb_u:.4f}(BB上軌) 禁空")
            sig, conf, reason, inv = "HOLD", 40, "價格>BB上軌禁空", ""
            quality = "C"

        # ── 動能耗竭過濾器 ──
        # 空頭: 價格已破 BB 下軌 或 RSI < 35 → 動能耗竭，不追空（等回彈）
        # 多頭: 價格已破 BB 上軌 或 RSI > 65 → 動能耗竭，不追多（等回落）
        if sig == "SHORT" and (price < bb_l or rsi < 35):
            exhaustion_reason = f"RSI={rsi:.0f}<35" if rsi < 35 else "價格<BB下軌"
            logger.info(f"[耗竭過濾] {symbol} SHORT 拒絕 — {exhaustion_reason}，等待回彈再空")
            sig, conf, reason, inv = "HOLD", 40, f"SHORT耗竭({exhaustion_reason})", ""
            quality = "C"
        elif sig == "LONG" and (price > bb_u or rsi > 65):
            exhaustion_reason = f"RSI={rsi:.0f}>65" if rsi > 65 else "價格>BB上軌"
            logger.info(f"[耗竭過濾] {symbol} LONG 拒絕 — {exhaustion_reason}，等待回落再多")
            sig, conf, reason, inv = "HOLD", 40, f"LONG耗竭({exhaustion_reason})", ""
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
