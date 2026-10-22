"""Live data the bot keeps for each trading pair."""
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SymbolState:
    symbol: str                     # Binance id, e.g. "BTCUSDT"
    ccxt_symbol: str                # ccxt id, e.g. "BTC/USDT:USDT"
    candles_4h: list = field(default_factory=list)                     # closed 4H candles
    live_4h: list | None = None                                        # 4H candle still forming
    candles_5m: deque = field(default_factory=lambda: deque(maxlen=500))  # closed 5m candles
    live_5m: list | None = None                                        # 5m candle still forming
    zones: list = field(default_factory=list)                          # current 4H liquidity zones
    atr_4h: float = 0.0
    armed_long: dict | None = None  # LONG setup waiting for the reclaim candle
    last_closed_checked: int = 0    # last closed 5m candle used for the LONG reclaim check
    cooldown_until: float = 0.0     # unix seconds; no new setup on this pair before this time
    last_price: float = 0.0
    tick_high: float | None = None  # highest price seen since the last evaluation (for exits)
    tick_low: float | None = None
    updated: bool = False
    volume_24h: float = 0.0         # 24h traded value in USDT (refreshed every PAIR_REFRESH_MIN)           # new market data arrived since the last evaluation
