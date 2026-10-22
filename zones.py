"""
RULE 1 - Higher timeframe (4H) liquidity map.

From CLOSED 4H candles we mark:
  * swing highs / swing lows        (stops of traders sit just beyond them)
  * equal highs / equal lows        (several swing points at the same price = more stops)
  * previous day high / low         (PDH / PDL)
  * previous week high / low        (PWH / PWL)
  * strong order blocks             (last opposite candle before a strong impulse move)

Only "fresh" zones are kept: the liquidity beyond them has NOT been taken yet
(no later 4H candle traded through the zone). Overlapping zones of the same type
are merged and their strength is added up. A zone must reach MIN_ZONE_STRENGTH
to be used, so a single lonely swing point is not enough on its own.

Strength points: swing point = 1, PDH/PDL = 2, PWH/PWL = 2, order block = 2,
confirmed liquidity heatmap cluster = +1 (optional).
"""
from collections import Counter
from dataclasses import dataclass, field

from .indicators import atr
from .utils import C, H, L, O, T, fmt_price

DAY_MS = 86_400_000


@dataclass
class Zone:
    kind: str            # "resistance" (supply, liquidity above) or "support" (demand, liquidity below)
    bottom: float
    top: float
    strength: int
    sources: list = field(default_factory=list)
    formed_at: int = 0   # index of the 4H candle where the zone became known

    def overlaps(self, other: "Zone") -> bool:
        return self.kind == other.kind and self.bottom <= other.top and other.bottom <= self.top

    def label(self) -> str:
        counts = Counter(self.sources)
        parts = []
        for src, n in counts.items():
            if src == "swing high" and n >= 2:
                parts.append(f"equal highs x{n}")
            elif src == "swing low" and n >= 2:
                parts.append(f"equal lows x{n}")
            else:
                parts.append(src)
        return ", ".join(parts)

    def describe(self) -> str:
        return f"{self.kind} {fmt_price(self.bottom)} - {fmt_price(self.top)} ({self.label()})"


def _day(ts: int) -> int:
    return ts // DAY_MS


def _week(ts: int) -> int:
    # 1970-01-01 was a Thursday; +3 makes weeks start on Monday (like Binance / TradingView)
    return (_day(ts) + 3) // 7


def build_zones(candles: list, now_ms: int, cfg) -> tuple[list, float]:
    """candles = CLOSED 4H candles, oldest first. Returns (zones, 4H ATR)."""
    n = len(candles)
    if n < 50:
        return [], 0.0
    highs = [c[H] for c in candles]
    lows = [c[L] for c in candles]
    closes = [c[C] for c in candles]
    a = atr(highs, lows, closes, 14)
    if a <= 0:
        return [], 0.0
    half = cfg.zone_half_width_atr * a
    raw: list[Zone] = []

    # --- swing highs / lows (equal highs & lows appear when several of these merge) ---
    p = cfg.pivot_len
    for i in range(p, n - p):
        if highs[i] == max(highs[i - p:i + p + 1]):
            raw.append(Zone("resistance", highs[i] - half, highs[i] + half, 1, ["swing high"], i + p))
        if lows[i] == min(lows[i - p:i + p + 1]):
            raw.append(Zone("support", lows[i] - half, lows[i] + half, 1, ["swing low"], i + p))

    # --- previous day / previous week high and low ---
    by_day, by_week = {}, {}
    for idx, c in enumerate(candles):
        by_day.setdefault(_day(c[T]), []).append(idx)
        by_week.setdefault(_week(c[T]), []).append(idx)
    for groups, key, tag in ((by_day, _day(now_ms) - 1, "PD"), (by_week, _week(now_ms) - 1, "PW")):
        idxs = groups.get(key)
        if not idxs:
            continue
        hi = max(highs[i] for i in idxs)
        lo = min(lows[i] for i in idxs)
        raw.append(Zone("resistance", hi - half, hi + half, 2, [f"{tag}H"], idxs[-1]))
        raw.append(Zone("support", lo - half, lo + half, 2, [f"{tag}L"], idxs[-1]))

    # --- strong order blocks (last opposite candle before an impulse of OB_IMPULSE_ATR) ---
    impulse = cfg.ob_impulse_atr * a
    for i in range(max(1, n - 120), n - 3):
        o, h, l, c = candles[i][O], candles[i][H], candles[i][L], candles[i][C]
        next_closes = closes[i + 1:i + 4]
        if c < o and max(next_closes) - h >= impulse:          # bearish candle, then strong rally
            raw.append(Zone("support", l, min(o, l + a), 2, ["bullish order block"], i + 3))
        if c > o and l - min(next_closes) >= impulse:          # bullish candle, then strong drop
            raw.append(Zone("resistance", max(o, h - a), h, 2, ["bearish order block"], i + 3))

    # --- keep only zones whose liquidity is still there (not swept by a later candle) ---
    fresh = []
    for z in raw:
        later = range(z.formed_at + 1, n)
        if z.kind == "resistance":
            swept = any(highs[j] > z.top for j in later)
        else:
            swept = any(lows[j] < z.bottom for j in later)
        if swept and not cfg.allow_swept_zones:
            continue
        fresh.append(z)

    # --- merge overlapping zones of the same type, add up strength ---
    merged: list[Zone] = []
    for z in sorted(fresh, key=lambda z: (z.kind, z.bottom)):
        if merged and merged[-1].overlaps(z):
            m = merged[-1]
            m.bottom, m.top = min(m.bottom, z.bottom), max(m.top, z.top)
            m.strength += z.strength
            m.sources += z.sources
            m.formed_at = max(m.formed_at, z.formed_at)
        else:
            merged.append(Zone(z.kind, z.bottom, z.top, z.strength, list(z.sources), z.formed_at))

    last = closes[-1]
    max_dist = last * cfg.max_zone_distance_pct / 100
    zones = [
        z for z in merged
        if z.strength >= cfg.min_zone_strength
        and min(abs(z.bottom - last), abs(z.top - last)) <= max_dist
    ]
    zones.sort(key=lambda z: -z.strength)
    return zones, a
