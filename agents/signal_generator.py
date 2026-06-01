"""
信號生成代理人 (SignalGeneratorAgent) V2.0
純技術指標 (TA-based) 規則引擎，零延遲，不呼叫 Gemini。
"""
from typing import Dict
from utils.logger import get_logger

logger = get_logger("SignalGenerator")


class SignalGeneratorAgent:
    def __init__(self, gemini=None):  # gemini 參數保留以維持呼叫介面相容
        pass

    async def generate(self, symbol: str, market_analysis: Dict,
                       indicators: Dict[str, float],
                       current_price: float) -> Dict:
        return self._rule_generate(symbol, market_analysis, indicators, current_price)

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

        # 2. BB 絕對禁飛區: 價格在帶外 → 全面禁止開倉（防止追高殺低）
        if price > bb_u:
            logger.debug(f"[BB禁飛] {symbol} 價格{price:.2f}>{bb_u:.2f} BB上軌，禁止開倉")
            return {**_hold, "reasoning": "價格>BB上軌禁止開倉"}
        if price < bb_l:
            logger.debug(f"[BB禁飛] {symbol} 價格{price:.2f}<{bb_l:.2f} BB下軌，禁止開倉")
            return {**_hold, "reasoning": "價格<BB下軌禁止開倉"}

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

        # BB 帶外已由前置過濾器（第 2 道）完全攔截，此處無需重複檢查

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
