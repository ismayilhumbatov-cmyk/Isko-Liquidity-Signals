"""
Configuration.

Every setting comes from environment variables (or a .env file in the project
folder). This is a SIGNAL-ONLY bot: it never places orders and needs no Binance keys. Nothing secret is ever written in the code, so the GitHub repo can stay
public as long as the .env file is never committed (it is in .gitignore).
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # .env on the server: Telegram token + chat ID (secret)
# settings.env comes from GitHub and holds the strategy numbers (no secrets).
# It is loaded last, so its values win. Edit it on GitHub, then re-run the installer.
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
    # Uses public Binance market data, no API keys and no orders.
    ws_url: str = field(default_factory=lambda: _str("WS_URL", ""))           # empty = automatic
    data_source: str = field(default_factory=lambda: _str("DATA_SOURCE", "ws").lower())  # ws | rest
    rest_poll_sec: float = field(default_factory=lambda: _float("REST_POLL_SEC", 10))

    # -------------------------------------------------------------- telegram
    telegram_token: str = field(default_factory=lambda: _str("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _str("TELEGRAM_CHAT_ID"))
    notify_armed: bool = field(default_factory=lambda: _bool("NOTIFY_ARMED", False))

    # ----------------------------------------------------------------- pairs
    # SCAN_ALL=true: scan EVERY Binance USDT perpetual crypto coin that has enough 24h volume.
    # SCAN_ALL=false: scan only the coins in PAIRS (the volume rule still applies).
    scan_all: bool = field(default_factory=lambda: _bool("SCAN_ALL", True))
    min_24h_volume: float = field(default_factory=lambda: _float("MIN_24H_VOLUME_USDT", 20_000_000))
    pair_refresh_min: float = field(default_factory=lambda: _float("PAIR_REFRESH_MIN", 60))
    max_pairs: int = field(default_factory=lambda: _int("MAX_PAIRS", 0))      # 0 = no limit
    pairs: list = field(default_factory=lambda: _list("PAIRS", "BTCUSDT,ETHUSDT,SOLUSDT"))
    exclude_pairs: list = field(default_factory=lambda: _list("EXCLUDE_PAIRS", ""))

    # ------------------------------------------- shown in the signal message
    risk_pct: float = field(default_factory=lambda: _float("RISK_PCT", 1.0))
    leverage: int = field(default_factory=lambda: _int("LEVERAGE", 10))
    max_sl_pct: float = field(default_factory=lambda: _float("MAX_SL_PCT", 4.0))   # skip setups whose stop is wider than this %

    # -------------------------------------------------------- 4H zone mapping
    htf_candles: int = field(default_factory=lambda: _int("HTF_CANDLES", 300))
    pivot_len: int = field(default_factory=lambda: _int("PIVOT_LEN", 3))
    zone_half_width_atr: float = field(default_factory=lambda: _float("ZONE_HALF_WIDTH_ATR", 0.15))
    ob_impulse_atr: float = field(default_factory=lambda: _float("OB_IMPULSE_ATR", 1.5))
    min_zone_strength: int = field(default_factory=lambda: _int("MIN_ZONE_STRENGTH", 2))
    allow_swept_zones: bool = field(default_factory=lambda: _bool("ALLOW_SWEPT_ZONES", False))
    max_zone_distance_pct: float = field(default_factory=lambda: _float("MAX_ZONE_DISTANCE_PCT", 12))
    zone_proximity_atr: float = field(default_factory=lambda: _float("ZONE_PROXIMITY_ATR", 0.3))
    max_overshoot_atr: float = field(default_factory=lambda: _float("MAX_OVERSHOOT_ATR", 0.6))
    htf_climax_mult: float = field(default_factory=lambda: _float("HTF_CLIMAX_VOL_MULT", 2.5))

    # ---------------------------------------------------------- 5m execution
    rsi_period: int = field(default_factory=lambda: _int("RSI_PERIOD", 14))
    rsi_short: float = field(default_factory=lambda: _float("RSI_SHORT_LEVEL", 80))
    rsi_long: float = field(default_factory=lambda: _float("RSI_LONG_LEVEL", 30))
    rsi_long_strong: float = field(default_factory=lambda: _float("RSI_LONG_STRONG", 25))
    big_candle_atr: float = field(default_factory=lambda: _float("BIG_CANDLE_ATR", 1.5))
    push_atr: float = field(default_factory=lambda: _float("PUSH_ATR", 2.0))
    upper_part: float = field(default_factory=lambda: _float("UPPER_PART", 0.7))
    vol_expansion: float = field(default_factory=lambda: _float("VOL_EXPANSION_MULT", 1.5))
    reclaim_vol: float = field(default_factory=lambda: _float("RECLAIM_VOL_MULT", 1.0))
    reclaim_atr: float = field(default_factory=lambda: _float("RECLAIM_ATR", 0.3))
    arm_expiry_bars: int = field(default_factory=lambda: _int("ARM_EXPIRY_BARS", 6))
    min_candle_progress: float = field(default_factory=lambda: _float("MIN_CANDLE_PROGRESS", 0.2))

    # ------------------------------------------------------- stops / targets
    sl_buffer_atr: float = field(default_factory=lambda: _float("SL_BUFFER_ATR", 0.2))
    min_sl_atr: float = field(default_factory=lambda: _float("MIN_SL_ATR", 0.5))
    min_sl_pct: float = field(default_factory=lambda: _float("MIN_SL_PCT", 0.3))      # keeps fees small vs risk
    sweep_room: float = field(default_factory=lambda: _float("SWEEP_ROOM", 0.25))     # SHORT: extra room beyond candle high, as part of candle size
    tp1_r: float = field(default_factory=lambda: _float("TP1_R", 2.0))
    tp2_r: float = field(default_factory=lambda: _float("TP2_R", 3.0))
    tp1_close_pct: float = field(default_factory=lambda: _float("TP1_CLOSE_PCT", 50))
    use_liquidity_targets: bool = field(default_factory=lambda: _bool("USE_LIQUIDITY_TARGETS", True))
    min_tp1_r: float = field(default_factory=lambda: _float("MIN_TP1_R", 1.0))
    max_liq_tp_r: float = field(default_factory=lambda: _float("MAX_LIQ_TP_R", 6.0))
    move_sl_to_be: bool = field(default_factory=lambda: _bool("MOVE_SL_TO_BE_AFTER_TP1", True))

    # --------------------------------------------------------------- runtime
    cooldown_min: float = field(default_factory=lambda: _float("COOLDOWN_MINUTES", 30))
    eval_interval_sec: float = field(default_factory=lambda: _float("EVAL_INTERVAL_SEC", 2))
    coinglass_api_key: str = field(default_factory=lambda: _str("COINGLASS_API_KEY"))
    status_hours: float = field(default_factory=lambda: _float("STATUS_HOURS", 6))   # 0 = off
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())

    def __post_init__(self):
        if self.tp2_r <= self.tp1_r:
            raise ValueError("TP2_R must be larger than TP1_R")
        if not 0 < self.tp1_close_pct <= 100:
            raise ValueError("TP1_CLOSE_PCT must be between 1 and 100")
