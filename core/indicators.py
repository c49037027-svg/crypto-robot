"""
技術指標計算模組 (純 pandas/numpy 實作，無需外部 TA 函式庫)

計算的指標:
  - EMA (9, 21, 55, 200)
  - RSI (14)
  - MACD (12, 26, 9)
  - Bollinger Bands (20, 2)
  - ATR (14) — 用於 SL/TP 計算
  - Volume SMA (20)
  - Stochastic RSI (14)
"""
import pandas as pd
import numpy as np
from typing import Dict


# ─────────────────────────────────────────────
# 基礎指標計算函式
# ─────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper, sma, lower


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — > 25 趨勢市，< 20 震盪市"""
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)

    plus_dm  = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    mask = plus_dm >= minus_dm
    plus_dm  = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)

    atr_val  = atr(high, low, close, period)
    eps = 1e-10
    plus_di  = 100 * plus_dm.ewm( com=period-1, adjust=False).mean() / (atr_val + eps)
    minus_di = 100 * minus_dm.ewm(com=period-1, adjust=False).mean() / (atr_val + eps)
    di_sum   = (plus_di + minus_di).replace(0, eps)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(com=period-1, adjust=False).mean()


def stoch_rsi(series: pd.Series, rsi_period: int = 14, stoch_period: int = 14) -> pd.Series:
    rsi_vals = rsi(series, rsi_period)
    min_rsi = rsi_vals.rolling(stoch_period).min()
    max_rsi = rsi_vals.rolling(stoch_period).max()
    denom = (max_rsi - min_rsi).replace(0, np.nan)
    return (rsi_vals - min_rsi) / denom * 100


# ─────────────────────────────────────────────
# 主要入口函式
# ─────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """
    輸入: OHLCV DataFrame，欄位需有 open/high/low/close/volume
    輸出: 所有指標的 Dict (最新值可用 ind['ema9'].iloc[-1] 取得)
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    ind: Dict[str, pd.Series] = {}

    # 趨勢指標
    ind["ema9"]   = ema(c, 9)
    ind["ema21"]  = ema(c, 21)
    ind["ema55"]  = ema(c, 55)
    ind["ema200"] = ema(c, 200)

    # 動量指標
    ind["rsi"] = rsi(c, 14)
    macd_line, sig_line, hist = macd(c)
    ind["macd"]        = macd_line
    ind["macd_signal"] = sig_line
    ind["macd_hist"]   = hist

    # 波動率指標
    bb_upper, bb_mid, bb_lower = bollinger_bands(c, 20, 2)
    ind["bb_upper"] = bb_upper
    ind["bb_mid"]   = bb_mid
    ind["bb_lower"] = bb_lower
    ind["atr"]      = atr(h, l, c, 14)

    # 成交量
    ind["vol_sma20"] = v.rolling(20).mean()
    ind["volume"]    = v

    # Stochastic RSI
    ind["stoch_rsi"] = stoch_rsi(c)

    # ADX (趨勢強度)
    ind["adx"] = adx(h, l, c, 14)

    # 收盤價 (供多時框比對用)
    ind["close"] = c

    return ind


def get_latest(ind: Dict[str, pd.Series]) -> Dict[str, float]:
    """取得所有指標的最新值"""
    return {k: float(v.iloc[-1]) if not pd.isna(v.iloc[-1]) else 0.0 for k, v in ind.items()}


def summarize_indicators(latest: Dict[str, float], current_price: float) -> str:
    """
    將指標轉換成自然語言摘要，供 AI Agent 分析使用
    """
    rsi_val = latest.get("rsi", 50)
    rsi_state = "超買" if rsi_val > 70 else ("超賣" if rsi_val < 30 else "中性")

    macd_bull = latest.get("macd", 0) > latest.get("macd_signal", 0)
    macd_state = "多頭" if macd_bull else "空頭"

    price = current_price
    ema200 = latest.get("ema200", price)
    trend = "上升趨勢 (價格高於 EMA200)" if price > ema200 else "下降趨勢 (價格低於 EMA200)"

    ema9 = latest.get("ema9", price)
    ema21 = latest.get("ema21", price)
    ema_cross = "黃金交叉 (EMA9 > EMA21)" if ema9 > ema21 else "死亡交叉 (EMA9 < EMA21)"

    bb_upper = latest.get("bb_upper", price)
    bb_lower = latest.get("bb_lower", price)
    bb_mid   = latest.get("bb_mid", price)
    if price > bb_upper:
        bb_state = "突破布林上軌 (超買壓力)"
    elif price < bb_lower:
        bb_state = "突破布林下軌 (超賣機會)"
    else:
        bb_state = f"在布林帶中 (偏{'上' if price > bb_mid else '下'})"

    vol_ratio = latest.get("volume", 0) / (latest.get("vol_sma20", 1) + 1e-9)
    vol_state = f"成交量 {'放大' if vol_ratio > 1.2 else ('萎縮' if vol_ratio < 0.8 else '正常')} ({vol_ratio:.1f}x 均量)"

    atr_val = latest.get("atr", 0)
    atr_pct = atr_val / price * 100 if price > 0 else 0

    stoch = latest.get("stoch_rsi", 50)
    stoch_state = "超買" if stoch > 80 else ("超賣" if stoch < 20 else "中性")

    return f"""
[技術指標摘要]
當前價格: {price:,.4f} USDT
趨勢: {trend}
EMA 交叉: {ema_cross} (EMA9={ema9:.4f}, EMA21={ema21:.4f}, EMA200={ema200:.4f})
RSI(14): {rsi_val:.1f} → {rsi_state}
MACD: {macd_state} (MACD={latest.get('macd', 0):.6f}, Signal={latest.get('macd_signal', 0):.6f})
布林帶: {bb_state} (上={bb_upper:.4f}, 中={bb_mid:.4f}, 下={bb_lower:.4f})
ATR(14): {atr_val:.4f} ({atr_pct:.2f}% of price)
{vol_state}
Stochastic RSI: {stoch:.1f} → {stoch_state}
""".strip()
