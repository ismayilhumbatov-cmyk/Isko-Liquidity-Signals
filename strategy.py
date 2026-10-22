"""
RULES 2 + 3 - 5-minute execution inside a valid 4H liquidity zone, with volume filters.

SHORT (upper liquidity sweep) - enters EARLY, while the big green candle is still pushing:
  1. price is at / inside a fresh 4H resistance zone (a small overshoot above it is allowed)
  2. the live 5m candle is a big bullish candle (body >= BIG_CANDLE_ATR x 5m ATR)
     or a strong push (last 2-3 green candles moved >= PUSH_ATR x ATR)
  3. price sits in the upper part of that candle (UPPER_PART, default top 30%)
  4. 5m RSI(14) > 80
  5. volume of the live candle is expanding (projected volume >= VOL_EXPANSION_MULT x 20-candle average)
  6. 4H volume is normal (no climax spike that already cleared the zone)
  -> SHORT at market. Stop just above the candle high (or above the zone if that is tighter).

LONG (lower liquidity sweep) - two steps, as in the rules:
  Step A "ARM": price is at / inside a fresh 4H support zone, a big red candle is pushing down,
                5m RSI(14) <= 30 (<= 25 = stronger), volume expanding, 4H volume normal.
                The bot does NOT buy yet. It follows the sweep low.
  Step B "TRIGGER": the first closed 5m candle that
                * is green,
                * closed back above the zone floor and RECLAIM_ATR x ATR above the sweep low,
                * has volume >= RECLAIM_VOL_MULT x average (volume shift to buyers),
                * shows RSI turning up from its low,
                -> LONG at market. Stop just below the sweep low (or below the zone if tighter).
  The armed setup is cancelled if no reclaim comes within ARM_EXPIRY_BARS candles
  or if a 5m candle closes far below the zone (the zone failed).

RULE 4 targets:
  TP1 (TP1_CLOSE_PCT of the position) = the opposite 4H liquidity zone if it is between
  MIN_TP1_R and MAX_LIQ_TP_R away, otherwise TP1_R x risk.
  TP2 (rest) = the next liquidity zone after that, otherwise TP2_R x risk.
"""
import logging
from dataclasses import dataclass, field

from .indicators import atr, rsi_series
from .state import SymbolState
from .utils import C, H, L, O, T, TF_MS, V, fmt_price
from .zones import Zone

log = logging.getLogger("strategy")


@dataclass
class Signal:
    symbol: str
    ccxt_symbol: str
    side: str                 # "LONG" or "SHORT"
    entry: float
    sl: float
    tp1: float
    tp2: float
    zone: Zone
    rsi: float
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

    # ------------------------------------------------------------------ main
    def evaluate(self, st: SymbolState, now_ms: int) -> Signal | None:
        cfg = self.cfg
        if st.live_5m is None or len(st.candles_5m) < 60 or not st.zones or st.atr_4h <= 0:
            return None
        if now_ms / 1000 < st.cooldown_until:
            return None

        closed = list(st.candles_5m)
        live = st.live_5m
        closes = [c[C] for c in closed] + [live[C]]
        rsi_vals = rsi_series(closes, cfg.rsi_period)
        rsi_now = float(rsi_vals[-1])
        atr5 = atr([c[H] for c in closed], [c[L] for c in closed], [c[C] for c in closed], 14)
        if atr5 <= 0 or rsi_now != rsi_now:  # NaN check
            return None

        recent_vols = [c[V] for c in closed[-20:]]
        vol_avg = sum(recent_vols) / len(recent_vols)
        progress = min(1.0, max(0.0, (now_ms - live[T]) / TF_MS["5m"]))
        projected_vol = live[V] / max(progress, cfg.min_candle_progress)
        vol_ratio = projected_vol / vol_avg if vol_avg > 0 else 0.0

        htf_ok, htf_note = self._htf_volume_ok(st, now_ms)
        ctx = dict(closed=closed, live=live, rsi_vals=rsi_vals, rsi=rsi_now, atr5=atr5,
                   vol_avg=vol_avg, vol_ratio=vol_ratio, htf_ok=htf_ok, htf_note=htf_note,
                   price=live[C], now_ms=now_ms)

        # 1) a LONG that is already armed waits for its reclaim candle
        if st.armed_long:
            return self._manage_armed_long(st, ctx)
        # 2) new SHORT sweep?
        sig = self._check_short(st, ctx)
        if sig:
            return sig
        # 3) new LONG sweep to arm?
        self._check_long_arm(st, ctx)
        return None

    # ----------------------------------------------------- RULE 3 (4H volume)
    def _htf_volume_ok(self, st: SymbolState, now_ms: int) -> tuple[bool, str]:
        c4 = st.candles_4h
        if len(c4) < 21:
            return True, "4H volume n/a"
        avg = sum(c[V] for c in c4[-21:-1]) / 20
        if avg <= 0:
            return True, "4H volume n/a"
        last_ratio = c4[-1][V] / avg
        live_ratio = 0.0
        if st.live_4h:
            progress = min(1.0, max(0.0, (now_ms - st.live_4h[T]) / TF_MS["4h"]))
            live_ratio = st.live_4h[V] / max(progress, 0.25) / avg
        ok = last_ratio <= self.cfg.htf_climax_mult and live_ratio <= self.cfg.htf_climax_mult
        return ok, f"4H volume {last_ratio:.1f}x last / {live_ratio:.1f}x live (limit {self.cfg.htf_climax_mult}x)"

    # ------------------------------------------------------------------ SHORT
    def _check_short(self, st: SymbolState, x: dict) -> Signal | None:
        cfg, live, closed, price, atr5 = self.cfg, x["live"], x["closed"], x["price"], x["atr5"]
        o, h, l, c = live[O], live[H], live[L], live[C]
        if c <= o:
            return None
        big = (c - o) >= cfg.big_candle_atr * atr5
        push = (closed[-1][C] > closed[-1][O]
                and (c - min(closed[-1][O], closed[-2][O])) >= cfg.push_atr * atr5)
        if not (big or push):
            return None
        rng = h - l
        if rng <= 0 or (c - l) / rng < cfg.upper_part:
            return None
        if x["rsi"] <= cfg.rsi_short:
            return None

        for z in st.zones:
            if z.kind != "resistance":
                continue
            if not (z.bottom - cfg.zone_proximity_atr * st.atr_4h <= price <= z.top + cfg.max_overshoot_atr * st.atr_4h):
                continue
            if x["vol_ratio"] < cfg.vol_expansion:
                log.debug("%s SHORT skipped: volume %.1fx < %.1fx", st.symbol, x["vol_ratio"], cfg.vol_expansion)
                return None
            if not x["htf_ok"]:
                log.info("%s SHORT skipped: %s", st.symbol, x["htf_note"])
                return None

            # Stop just beyond the sweeping candle's high, or beyond the zone if that is tighter.
            # We enter before the candle is finished, so the high can still grow a little:
            # SWEEP_ROOM adds part of the candle size as room above the high.
            buf = cfg.sl_buffer_atr * atr5
            min_dist = max(cfg.min_sl_atr * atr5, price * cfg.min_sl_pct / 100)
            candle_stop = h + max(buf, cfg.sweep_room * rng)
            candidates = [s for s in (candle_stop, z.top + buf) if s - price >= min_dist]
            sl = min(candidates) if candidates else price + min_dist
            if (sl - price) / price * 100 > cfg.max_sl_pct:
                log.info("%s SHORT skipped: stop too wide", st.symbol)
                return None

            tp1, tp2, note = self._targets("SHORT", price, sl, st)
            strong = x["rsi"] >= 85 and z.strength >= 3
            reasons = [
                f"4H zone: {z.describe()}",
                f"5m RSI {x['rsi']:.1f} > {cfg.rsi_short:g}",
                f"Big green 5m candle {(c - o) / atr5:.1f}x ATR, price in top {(1 - (c - l) / rng) * 100:.0f}% of it",
                f"5m volume {x['vol_ratio']:.1f}x average (expanding)",
                x["htf_note"],
            ]
            return Signal(st.symbol, st.ccxt_symbol, "SHORT", price, sl, tp1, tp2, z, x["rsi"],
                          "A+" if strong else "A", reasons, note, x["now_ms"])
        return None

    # --------------------------------------------------------- LONG step A
    def _check_long_arm(self, st: SymbolState, x: dict) -> None:
        cfg, live, closed, price, atr5 = self.cfg, x["live"], x["closed"], x["price"], x["atr5"]
        o, l, c = live[O], live[L], live[C]
        if c >= o:
            return
        big = (o - c) >= cfg.big_candle_atr * atr5
        push = (closed[-1][C] < closed[-1][O]
                and (max(closed[-1][O], closed[-2][O]) - c) >= cfg.push_atr * atr5)
        if not (big or push) or x["rsi"] > cfg.rsi_long:
            return
        if x["vol_ratio"] < cfg.vol_expansion or not x["htf_ok"]:
            return
        for z in st.zones:
            if z.kind != "support":
                continue
            if z.bottom - cfg.max_overshoot_atr * st.atr_4h <= price <= z.top + cfg.zone_proximity_atr * st.atr_4h:
                st.armed_long = {
                    "zone": z, "sweep_low": l, "armed_candle": live[T], "rsi_min": x["rsi"],
                    "sweep_vol": x["vol_ratio"], "armed_price": price,
                }
                st.last_closed_checked = closed[-1][T]
                log.info("%s LONG ARMED at %s | RSI %.1f | vol %.1fx | %s",
                         st.symbol, fmt_price(price), x["rsi"], x["vol_ratio"], z.describe())
                return

    # --------------------------------------------------------- LONG step B
    def _manage_armed_long(self, st: SymbolState, x: dict) -> Signal | None:
        cfg, a, live, closed = self.cfg, st.armed_long, x["live"], x["closed"]
        z: Zone = a["zone"]
        a["sweep_low"] = min(a["sweep_low"], live[L])
        a["rsi_min"] = min(a["rsi_min"], x["rsi"])

        if (live[T] - a["armed_candle"]) // TF_MS["5m"] > cfg.arm_expiry_bars:
            log.info("%s LONG setup expired (no reclaim)", st.symbol)
            st.armed_long = None
            return None

        last = closed[-1]
        if last[T] < a["armed_candle"] or last[T] == st.last_closed_checked:
            return None                       # wait for a new closed candle
        st.last_closed_checked = last[T]
        a["sweep_low"] = min(a["sweep_low"], last[L])

        if last[C] < z.bottom - cfg.max_overshoot_atr * st.atr_4h:
            log.info("%s LONG setup cancelled: closed below the zone (zone failed)", st.symbol)
            st.armed_long = None
            return None

        atr5, price = x["atr5"], x["price"]
        green = last[C] > last[O]
        reclaim = last[C] >= a["sweep_low"] + cfg.reclaim_atr * atr5 and last[C] >= z.bottom
        vol_shift = last[V] >= cfg.reclaim_vol * x["vol_avg"]
        rsi_last_closed = float(x["rsi_vals"][-2])
        rsi_turn = rsi_last_closed > a["rsi_min"] + 2
        if not (green and reclaim and vol_shift and rsi_turn):
            return None

        buf = cfg.sl_buffer_atr * atr5
        min_dist = max(cfg.min_sl_atr * atr5, price * cfg.min_sl_pct / 100)
        candidates = [s for s in (a["sweep_low"] - buf, z.bottom - buf) if price - s >= min_dist]
        sl = max(candidates) if candidates else price - min_dist
        st.armed_long = None
        if (price - sl) / price * 100 > cfg.max_sl_pct:
            log.info("%s LONG skipped: stop too wide", st.symbol)
            return None

        tp1, tp2, note = self._targets("LONG", price, sl, st)
        strong = a["rsi_min"] <= cfg.rsi_long_strong and z.strength >= 3
        reasons = [
            f"4H zone: {z.describe()}",
            f"5m RSI low {a['rsi_min']:.1f} (<= {cfg.rsi_long:g}{', strong' if a['rsi_min'] <= cfg.rsi_long_strong else ''}), now turning up",
            f"Sweep low {fmt_price(a['sweep_low'])}, sweep volume {a['sweep_vol']:.1f}x",
            f"Green reclaim candle, volume {last[V] / x['vol_avg']:.1f}x average",
            x["htf_note"],
        ]
        return Signal(st.symbol, st.ccxt_symbol, "LONG", price, sl, tp1, tp2, z, a["rsi_min"],
                      "A+" if strong else "A", reasons, note, x["now_ms"])

    # ---------------------------------------------------------------- targets
    def _targets(self, side: str, entry: float, sl: float, st: SymbolState) -> tuple[float, float, str]:
        cfg = self.cfg
        risk = abs(entry - sl)
        sign = 1 if side == "LONG" else -1
        tp1 = entry + sign * cfg.tp1_r * risk
        tp2 = entry + sign * cfg.tp2_r * risk
        note = f"fixed {cfg.tp1_r:g}R / {cfg.tp2_r:g}R"
        if not cfg.use_liquidity_targets:
            return tp1, tp2, note
        if side == "LONG":
            levels = sorted(z.bottom for z in st.zones if z.kind == "resistance" and z.bottom > entry)
        else:
            levels = sorted((z.top for z in st.zones if z.kind == "support" and z.top < entry), reverse=True)
        levels = [p for p in levels if cfg.min_tp1_r * risk <= abs(p - entry) <= cfg.max_liq_tp_r * risk]
        if levels:
            tp1 = levels[0]
            note = "TP1 at opposite 4H liquidity"
            if len(levels) > 1:
                tp2 = levels[1]
                note += ", TP2 at next liquidity"
            else:
                extra = (cfg.tp2_r - cfg.tp1_r) * risk
                tp2 = entry + sign * max(cfg.tp2_r * risk, abs(tp1 - entry) + extra)
                note += f", TP2 fixed"
        return tp1, tp2, note
