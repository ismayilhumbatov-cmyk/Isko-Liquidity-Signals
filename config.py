"""
Configuration.

Values come from (last one wins):
  1. the defaults written below
  2. .env on the server        - Telegram token and chat ID (secret, never on GitHub)
  3. settings.env from GitHub  - strategy numbers (edit it on GitHub, then re-run the installer)

This is a SIGNAL-ONLY bot: it never places orders and needs no Binance keys.
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()
_SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.env")
load_dotenv(_SETTINGS, override=True)


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _float(name: str, default: float) -> float:
    return float(_str(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(float(_str(name, str(default))))


def _bool(name: str, default: bool) -> bool:
    return _str(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list:
    return [x.strip().upper() for x in _str(name, default).split(",") if x.strip()]


@dataclass
class Config:
    # ---------------------------------------------------------- market data
    ws_url: str = field(default_factory=lambda: _str("WS_URL", ""))
    data_source: str = field(default_factory=lambda: _str("DATA_SOURCE", "ws").lower())   # ws | rest
    rest_poll_sec: float = field(default_factory=lambda: _float("REST_POLL_SEC", 10))

    # -------------------------------------------------------------- telegram
    telegram_token: str = field(default_factory=lambda: _str("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _str("TELEGRAM_CHAT_ID"))

    # ----------------------------------------------------------------- coins
    scan_all: bool = field(default_factory=lambda: _bool("SCAN_ALL", True))
    min_24h_volume: float = field(default_factory=lambda: _float("MIN_24H_VOLUME_USDT", 3_000_000))
    pair_refresh_min: float = field(default_factory=lambda: _float("PAIR_REFRESH_MIN", 60))
    max_pairs: int = field(default_factory=lambda: _int("MAX_PAIRS", 0))
    pairs: list = field(default_factory=lambda: _list("PAIRS", "BTCUSDT,ETHUSDT,SOLUSDT"))
    exclude_pairs: list = field(default_factory=lambda: _list("EXCLUDE_PAIRS", ""))

    # -------------------------------------------------- step 1: 4H trend
    trend_ema: int = field(default_factory=lambda: _int("TREND_EMA", 50))
    trend_slope_bars: int = field(default_factory=lambda: _int("TREND_SLOPE_BARS", 3))

    # ------------------------------------------------ step 2: 15m check
    use_15m_filter: bool = field(default_factory=lambda: _bool("USE_15M_FILTER", True))
    mid_ema: int = field(default_factory=lambda: _int("MID_EMA", 50))

    # ------------------------------------------- step 3: 1m cross/retest
    entry_ema: int = field(default_factory=lambda: _int("ENTRY_EMA", 50))
    retest_window_min: float = field(default_factory=lambda: _float("RETEST_WINDOW_MIN", 15))
    touch_tolerance_atr: float = field(default_factory=lambda: _float("TOUCH_TOLERANCE_ATR", 0.1))
    close_tolerance_atr: float = field(default_factory=lambda: _float("CLOSE_TOLERANCE_ATR", 0.25))
    big_candle_mult: float = field(default_factory=lambda: _float("BIG_CANDLE_MULT", 1.5))
    confirm_vol_mult: float = field(default_factory=lambda: _float("CONFIRM_VOL_MULT", 1.0))

    # ------------------------------------------------ stop / targets
    sl_buffer_atr: float = field(default_factory=lambda: _float("SL_BUFFER_ATR", 0.1))
    min_sl_pct: float = field(default_factory=lambda: _float("MIN_SL_PCT", 0.3))
    max_sl_pct: float = field(default_factory=lambda: _float("MAX_SL_PCT", 3.0))
    tp1_r: float = field(default_factory=lambda: _float("TP1_R", 1.5))
    tp2_r: float = field(default_factory=lambda: _float("TP2_R", 3.0))
    tp1_close_pct: float = field(default_factory=lambda: _float("TP1_CLOSE_PCT", 50))
    move_sl_to_be: bool = field(default_factory=lambda: _bool("MOVE_SL_TO_BE_AFTER_TP1", True))

    # ---------------------------------------------- shown in the message
    risk_pct: float = field(default_factory=lambda: _float("RISK_PCT", 1.0))
    leverage: int = field(default_factory=lambda: _int("LEVERAGE", 10))

    # --------------------------------------------------------------- runtime
    cooldown_min: float = field(default_factory=lambda: _float("COOLDOWN_MINUTES", 30))
    eval_interval_sec: float = field(default_factory=lambda: _float("EVAL_INTERVAL_SEC", 2))
    status_hours: float = field(default_factory=lambda: _float("STATUS_HOURS", 6))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())

    def __post_init__(self):
        if self.tp2_r <= self.tp1_r:
            raise ValueError("TP2_R must be larger than TP1_R")
        if not 0 < self.tp1_close_pct <= 100:
            raise ValueError("TP1_CLOSE_PCT must be between 1 and 100")
