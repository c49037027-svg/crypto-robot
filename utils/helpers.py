"""
通用工具函數
"""
from datetime import datetime, timezone
import json
from typing import Any


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def format_pct(value: float, decimals: int = 2) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def format_price(value: float, decimals: int = 4) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    return f"{value:.{decimals}f}"


def format_usdt(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.2f} USDT"


def safe_json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str, indent=2)
    except Exception:
        return str(data)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100
