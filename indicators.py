"""
Indicators.

RSI and ATR both use Wilder's smoothing, which is the same method Binance and
TradingView use, so the RSI value in the bot matches the RSI you see on the chart
(as long as enough history is loaded - the bot loads 300 candles).
"""
import numpy as np


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_series(closes, period: int = 14) -> np.ndarray:
    """RSI for every candle (NaN until enough data)."""
    c = np.asarray(closes, dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) < period + 1:
        return out
    diff = np.diff(c)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_g = gains[:period].mean()
    avg_l = losses[:period].mean()
    out[period] = _rsi_from_avgs(avg_g, avg_l)
    for i in range(period, len(diff)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avgs(avg_g, avg_l)
    return out


def atr(highs, lows, closes, period: int = 14) -> float:
    """Latest Average True Range value (how much price normally moves per candle)."""
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    if len(c) < 2:
        return 0.0
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    if len(tr) < period:
        return float(tr.mean())
    value = tr[:period].mean()
    for x in tr[period:]:
        value = (value * (period - 1) + x) / period
    return float(value)


def ema_series(values, period: int = 50) -> list:
    """Exponential moving average for every candle (None until enough data).
    Starts from the simple average of the first `period` values, like TradingView."""
    v = [float(x) for x in values]
    out = [None] * len(v)
    if len(v) < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(v[:period]) / period
    out[period - 1] = e
    for i in range(period, len(v)):
        e = v[i] * k + e * (1 - k)
        out[i] = e
    return out


def market_structure(candles, pivot: int = 3) -> tuple:
    """Market structure from closed candles [t, o, h, l, c, v].
    A swing high/low is confirmed once `pivot` candles on each side are lower/higher.
    A candle CLOSING above the last swing high turns the structure 'bull';
    closing below the last swing low turns it 'bear' (a market structure shift / CHoCH).
    Returns (state, time_of_last_shift, direction_of_last_shift, last_swing_high, last_swing_low)."""
    n = len(candles)
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    swing_high = swing_low = None
    state = shift_ts = shift_dir = None
    for i in range(n):
        j = i - pivot
        if j >= pivot:
            if highs[j] == max(highs[j - pivot:j + pivot + 1]):
                swing_high = highs[j]
            if lows[j] == min(lows[j - pivot:j + pivot + 1]):
                swing_low = lows[j]
        close = candles[i][4]
        if swing_high is not None and close > swing_high and state != "bull":
            state, shift_ts, shift_dir = "bull", candles[i][0], "bull"
        elif swing_low is not None and close < swing_low and state != "bear":
            state, shift_ts, shift_dir = "bear", candles[i][0], "bear"
    return state, shift_ts, shift_dir, swing_high, swing_low
