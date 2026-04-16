"""
量化交易機器人 - 配置模組
所有設定統一由此讀取
"""
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def _bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")

def _float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default

def _int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


@dataclass
class ExchangeConfig:
    name: str = field(default_factory=lambda: _env("EXCHANGE_NAME", "binance"))
    api_key: str = field(default_factory=lambda: _env("EXCHANGE_API_KEY"))
    api_secret: str = field(default_factory=lambda: _env("EXCHANGE_API_SECRET"))
    testnet: bool = field(default_factory=lambda: _bool("TESTNET", True))
    paper_trading: bool = field(default_factory=lambda: _bool("PAPER_TRADING", True))
    paper_balance: float = field(default_factory=lambda: _float("PAPER_BALANCE", 10000.0))
    leverage: int = field(default_factory=lambda: _int("LEVERAGE", 1))


@dataclass
class TradingConfig:
    symbols: List[str] = field(
        default_factory=lambda: [s.strip() for s in _env("SYMBOLS", "BTC/USDT,ETH/USDT").split(",")]
    )
    primary_timeframe: str = field(default_factory=lambda: _env("PRIMARY_TIMEFRAME", "1h"))
    confirmation_timeframes: List[str] = field(default_factory=lambda: ["4h", "1d"])
    scan_interval: int = field(default_factory=lambda: _int("SCAN_INTERVAL", 60))
    ohlcv_limit: int = 300


@dataclass
class RiskConfig:
    """
    風險管理核心設定
    止損 = entry ± atr_sl_multiplier * ATR
    止盈 = entry ± atr_tp_multiplier * ATR
    預設 2:3 ATR 倍數 → 盈虧比 1:1.5
    """
    risk_per_trade_pct: float = field(default_factory=lambda: _float("RISK_PER_TRADE", 1.0))
    max_positions: int = field(default_factory=lambda: _int("MAX_POSITIONS", 3))
    max_daily_loss_pct: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS", 3.0))
    max_drawdown_pct: float = field(default_factory=lambda: _float("MAX_DRAWDOWN", 10.0))
    min_risk_reward: float = field(default_factory=lambda: _float("MIN_RISK_REWARD", 1.5))
    atr_sl_multiplier: float = 2.0
    atr_tp_multiplier: float = 3.0
    trailing_stop: bool = True
    trailing_stop_atr_mult: float = 1.5
    max_position_age_bars: int = 48


@dataclass
class AgentConfig:
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-1.5-flash"))
    min_confidence: float = field(default_factory=lambda: _float("MIN_CONFIDENCE", 65.0))


@dataclass
class BotConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    dashboard_port: int = field(default_factory=lambda: _int("DASHBOARD_PORT", 8080))
    log_level: str = "INFO"

    def validate(self) -> List[str]:
        issues = []
        if not self.exchange.paper_trading:
            if not self.exchange.api_key:
                issues.append("❌ 缺少 EXCHANGE_API_KEY")
            if not self.exchange.api_secret:
                issues.append("❌ 缺少 EXCHANGE_API_SECRET")
        if self.risk.min_risk_reward < 1.5:
            issues.append(f"⚠️  盈虧比 {self.risk.min_risk_reward} 低於建議最低值 1.5")
        return issues


config = BotConfig()
