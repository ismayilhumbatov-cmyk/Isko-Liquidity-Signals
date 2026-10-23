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
