"""
Strategy: 4H trend + 15m check + 1-minute EMA 50 cross, retest and big candle.

STEP 1 - 4H trend (decides the direction)
  Uptrend   : last closed 4H candle above the 4H EMA 50, and the EMA 50 higher than 3 candles ago -> LONG only
  Downtrend : last closed 4H candle below the 4H EMA 50, and the EMA 50 lower than 3 candles ago  -> SHORT only
  Otherwise : no trade on this coin

STEP 2 - 15m check (USE_15M_FILTER)
  LONG  : last closed 15m candle above the 15m EMA 50
  SHORT : last closed 15m candle below the 15m EMA 50

STEP 3 - 1m entry (all within RETEST_WINDOW_MIN = 15 minutes after the cross)
  LONG:
   a) Cross   : a 1m candle closes above the 1m EMA 50 (the candle before closed at or below it)
   b) Retest  : a LATER candle touches the EMA 50 (its low reaches the EMA, a tiny tolerance allowed)
                and does not close clearly below it. It can be the very next candle.
   c) Confirm : a SEPARATE candle after the retest is a big green candle:
                body >= BIG_CANDLE_MULT x average body of the last 20 candles,
                volume >= CONFIRM_VOL_MULT x average volume,
                closes above the EMA 50 and above the high of the retest candle.
   -> LONG at the close of the confirmation candle.
  Cancelled if a candle closes clearly below the EMA 50, if 15 minutes pass,
  or if the 4H / 15m direction no longer agrees.
  SHORT: exactly the opposite.

STEP 4 - stop and targets
  Stop  : just beyond the retest wick (lowest retest low for LONG, highest retest high for SHORT),
          never closer than MIN_SL_PCT % from entry; setups with a stop wider than MAX_SL_PCT % are skipped.
  TP1   : TP1_R x risk (close TP1_CLOSE_PCT %, stop to entry)
  TP2   : TP2_R x risk (the rest)
"""
import logging
from collections import Counter
from dataclasses import dataclass, field

from .indicators import atr, ema_series
from .state import SymbolState
from .utils import C, H, L, O, T, V, fmt_price, utc_str

log = logging.getLogger("strategy")
MIN_MS = 60_000


@dataclass
class Signal:
    symbol: str
    ccxt_symbol: str
    side: str                 # "LONG" or "SHORT"
    entry: float
    sl: float
    tp1: float
    tp2: float
    quality: str              # "A+" or "A"
    reasons: list = field(default_factory=list)
    target_note: str = ""
    created_ms: int = 0

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def r_of(self, price: float) -> float:
        return abs(price - self.entry) / self.risk if self.risk else 0.0


class Strategy:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stats = Counter()        # crosses, retests, expired, failed... for the status report

    # ------------------------------------------------------------ trends
    def trend_4h(self, st: SymbolState) -> str | None:
        """'UP', 'DOWN' or None (no clear trend)."""
        c4 = list(st.candles["4h"])
        n = self.cfg.trend_slope_bars
        if len(c4) < self.cfg.trend_ema + n + 1:
            return None
        ema = ema_series([c[C] for c in c4], self.cfg.trend_ema)
        if ema[-1] is None or ema[-1 - n] is None:
            return None
        close = c4[-1][C]
        if close > ema[-1] and ema[-1] > ema[-1 - n]:
            return "UP"
        if close < ema[-1] and ema[-1] < ema[-1 - n]:
            return "DOWN"
        return None

    def check_15m(self, st: SymbolState, side: str) -> tuple[bool, float | None]:
        if not self.cfg.use_15m_filter:
            return True, None
        c15 = list(st.candles["15m"])
        if len(c15) < self.cfg.mid_ema + 1:
            return False, None
        ema = ema_series([c[C] for c in c15], self.cfg.mid_ema)[-1]
        close = c15[-1][C]
        ok = close > ema if side == "LONG" else close < ema
        return ok, ema

    def direction(self, st: SymbolState) -> tuple[str | None, str]:
        """Allowed side for this coin right now, plus a reason text."""
        trend = self.trend_4h(st)
        if trend is None:
            return None, "no clear 4H trend"
        side = "LONG" if trend == "UP" else "SHORT"
        ok15, _ = self.check_15m(st, side)
        if not ok15:
            return None, f"15m against 4H {trend.lower()}trend"
        return side, ""

    # -------------------------------------------------------------- main
    def evaluate(self, st: SymbolState, now_ms: int) -> Signal | None:
        """Runs on every new CLOSED 1m candle."""
        closed = list(st.candles["1m"])
        if len(closed) < self.cfg.entry_ema + 25:
            return None
        new = [c for c in closed if c[T] > st.last_closed_checked]
        if not new:
            return None
        st.last_closed_checked = closed[-1][T]
        if len(new) > 3:                  # after a data gap, only look at the latest candles
            new = new[-3:]

        ema = ema_series([c[C] for c in closed], self.cfg.entry_ema)
        signal = None
        for c in new:
            i = next(k for k in range(len(closed) - 1, -1, -1) if closed[k][T] == c[T])
            signal = self._step(st, closed, ema, i, now_ms) or signal
            if signal:
                break
        return signal

    def _step(self, st: SymbolState, closed: list, ema: list, i: int, now_ms: int) -> Signal | None:
        cfg = self.cfg
        c, prev = closed[i], closed[i - 1]
        e, e_prev = ema[i], ema[i - 1]
        if e is None or e_prev is None:
            return None
        window = closed[max(0, i - 20):i]
        a1 = atr([x[H] for x in closed[max(0, i - 60):i + 1]],
                 [x[L] for x in closed[max(0, i - 60):i + 1]],
                 [x[C] for x in closed[max(0, i - 60):i + 1]], 14)
        avg_body = sum(abs(x[C] - x[O]) for x in window) / len(window)
        avg_vol = sum(x[V] for x in window) / len(window)
        if a1 <= 0 or avg_body <= 0:
            return None
        touch_tol = cfg.touch_tolerance_atr * a1
        close_tol = cfg.close_tolerance_atr * a1

        side, why_not = self.direction(st)
        s = st.setup

        # ---- an active setup: check expiry, direction, failure, retest, confirmation
        if s:
            if c[T] - s["cross_ts"] > cfg.retest_window_min * MIN_MS:
                self.stats["expired"] += 1
                log.info("%s %s setup expired (no confirmation within %g min)", st.symbol, s["side"], cfg.retest_window_min)
                st.setup = s = None
            elif side != s["side"]:
                self.stats["cancelled_trend"] += 1
                log.info("%s %s setup cancelled: %s", st.symbol, s["side"], why_not or "direction changed")
                st.setup = s = None

        if s:
            long_ = s["side"] == "LONG"
            failed = c[C] < e - close_tol if long_ else c[C] > e + close_tol
            if failed:
                self.stats["failed"] += 1
                log.info("%s %s setup failed: closed back through EMA 50", st.symbol, s["side"])
                st.setup = s = None
            else:
                touched = c[L] <= e + touch_tol if long_ else c[H] >= e - touch_tol
                body = (c[C] - c[O]) if long_ else (c[O] - c[C])
                if s["phase"] == "confirm":
                    big = body >= cfg.big_candle_mult * avg_body
                    vol_ok = c[V] >= cfg.confirm_vol_mult * avg_vol
                    beyond = c[C] > max(e, s["retest_high"]) if long_ else c[C] < min(e, s["retest_low"])
                    if body > 0 and big and vol_ok and beyond:
                        return self._signal(st, c, e, s, body / avg_body, c[V] / avg_vol if avg_vol else 0, a1, now_ms)
                    if body > 0 and big and beyond and not vol_ok:
                        self.stats["low_volume"] += 1
                if touched:
                    if s["phase"] == "retest":
                        self.stats["retests"] += 1
                        log.info("%s %s retest of EMA 50 at %s", st.symbol, s["side"], fmt_price(e))
                    s["phase"] = "confirm"
                    s["retest_ts"] = c[T]
                    s["retest_low"] = min(s.get("retest_low", c[L]), c[L]) if long_ else c[L]
                    s["retest_high"] = c[H] if long_ else max(s.get("retest_high", c[H]), c[H])
                    s["stop_ref"] = min(s.get("stop_ref", c[L]), c[L]) if long_ else max(s.get("stop_ref", c[H]), c[H])
                return None

        # ---- no setup: look for a new cross in the allowed direction
        if side is None:
            return None
        crossed_up = prev[C] <= e_prev and c[C] > e
        crossed_down = prev[C] >= e_prev and c[C] < e
        if (side == "LONG" and crossed_up) or (side == "SHORT" and crossed_down):
            st.setup = {"side": side, "cross_ts": c[T], "phase": "retest", "cross_price": c[C]}
            self.stats["crosses"] += 1
            log.debug("%s %s cross of 1m EMA 50 at %s", st.symbol, side, fmt_price(c[C]))
        return None

    # ------------------------------------------------------------ signal
    def _signal(self, st: SymbolState, c: list, e: float, s: dict, body_x: float, vol_x: float,
                a1: float, now_ms: int) -> Signal | None:
        cfg = self.cfg
        long_ = s["side"] == "LONG"
        entry = c[C]
        buf = cfg.sl_buffer_atr * a1
        min_dist = entry * cfg.min_sl_pct / 100
        sl = s["stop_ref"] - buf if long_ else s["stop_ref"] + buf
        if abs(entry - sl) < min_dist:
            sl = entry - min_dist if long_ else entry + min_dist
        st.setup = None
        risk_pct = abs(entry - sl) / entry * 100
        if risk_pct > cfg.max_sl_pct:
            self.stats["stop_too_wide"] += 1
            log.info("%s %s skipped: stop %.2f%% wider than %g%%", st.symbol, s["side"], risk_pct, cfg.max_sl_pct)
            return None
        risk = abs(entry - sl)
        sign = 1 if long_ else -1
        tp1 = entry + sign * cfg.tp1_r * risk
        tp2 = entry + sign * cfg.tp2_r * risk

        trend_word = "uptrend: price above rising EMA 50" if long_ else "downtrend: price below falling EMA 50"
        mid = "above" if long_ else "below"
        colour = "green" if long_ else "red"
        reasons = [
            f"4H {trend_word}",
            f"15m: price {mid} EMA 50" if cfg.use_15m_filter else "15m check off",
            f"1m: crossed EMA 50 at {utc_str(s['cross_ts'])[11:16]} UTC, retest at {utc_str(s['retest_ts'])[11:16]} UTC",
            f"Big {colour} candle: body {body_x:.1f}x average, volume {vol_x:.1f}x average",
        ]
        quality = "A+" if body_x >= 2.5 and vol_x >= 2.0 else "A"
        self.stats["signals"] += 1
        return Signal(st.symbol, st.ccxt_symbol, s["side"], entry, sl, tp1, tp2, quality, reasons,
                      f"TP1 {cfg.tp1_r:g}R, TP2 {cfg.tp2_r:g}R", now_ms)
