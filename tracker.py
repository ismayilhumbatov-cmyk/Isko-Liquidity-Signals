"""
Signal tracker (NO TRADING).

The bot never places orders. After a signal is sent, it follows the price and
reports in Telegram when TP1, TP2, the stop loss or breakeven is reached, and
the final result of the signal in R (R = the distance from entry to stop).

* One open signal per pair at a time, any number of pairs at once.
* TP1 counts TP1_CLOSE_PCT % of the idea, TP2 the rest.
* After TP1 the stop moves to entry if MOVE_SL_TO_BE_AFTER_TP1=true.
* If the stop and a target are touched in the same moment, the stop is counted
  (the more careful result).
* Open signals are saved in state/signals.json, so a restart does not lose them.
"""
import json
import logging
import os
from dataclasses import asdict, dataclass

from .notifier import Telegram, closed_message, exit_message, signal_message
from .state import SymbolState
from .utils import Journal, now_ms

log = logging.getLogger("tracker")
STATE_FILE = "state/signals.json"


@dataclass
class TrackedSignal:
    symbol: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    risk: float               # entry-to-stop distance at the start
    opened_ms: int
    quality: str = "A"
    tp1_done: bool = False
    remaining: float = 1.0    # share of the idea still open (1.0 = 100%)
    result_r: float = 0.0     # result so far, in R


class SignalTracker:
    def __init__(self, cfg, notifier: Telegram, journal: Journal):
        self.cfg, self.tg, self.journal = cfg, notifier, journal
        self.open: dict[str, TrackedSignal] = {}
        self._load()

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        os.makedirs("state", exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(s) for s in self.open.values()], f, indent=2)
        os.replace(tmp, STATE_FILE)

    def _load(self) -> None:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                for row in json.load(f):
                    s = TrackedSignal(**row)
                    self.open[s.symbol] = s
            log.info("Restored %d open signals", len(self.open))
        except Exception as e:
            log.error("Could not read %s: %s", STATE_FILE, e)

    # --------------------------------------------------------------- signals
    def has_open(self, symbol: str) -> bool:
        return symbol in self.open

    async def publish(self, sig, volume_24h: float = 0.0) -> None:
        """Send the signal to Telegram and start following it."""
        if self.has_open(sig.symbol):
            return
        self.open[sig.symbol] = TrackedSignal(sig.symbol, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2,
                                              sig.risk, now_ms(), sig.quality)
        self._save()
        self.journal.write("signal", symbol=sig.symbol, side=sig.side, quality=sig.quality, entry=sig.entry,
                           sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2, volume_24h=round(volume_24h), reasons=sig.reasons)
        await self.tg.send(signal_message(sig, self.cfg, volume_24h))

    # ------------------------------------------------------- price following
    async def on_tick(self, st: SymbolState) -> None:
        s = self.open.get(st.symbol)
        if not s:
            return
        hi = st.tick_high if st.tick_high is not None else st.last_price
        lo = st.tick_low if st.tick_low is not None else st.last_price
        if not hi or not lo:
            return
        long_ = s.side == "LONG"
        sl_hit = lo <= s.sl if long_ else hi >= s.sl
        tp1_hit = hi >= s.tp1 if long_ else lo <= s.tp1
        tp2_hit = hi >= s.tp2 if long_ else lo <= s.tp2

        if sl_hit:
            reason = "BE" if s.tp1_done and s.sl == s.entry else "SL"
            await self._close_part(s, s.remaining, s.sl, reason)
            return
        if not s.tp1_done and tp1_hit:
            s.tp1_done = True
            share = min(self.cfg.tp1_close_pct / 100, s.remaining)
            moved_be = self.cfg.move_sl_to_be and s.remaining - share > 1e-9
            await self._close_part(s, share, s.tp1, "TP1", moved_be)
            if moved_be and s.symbol in self.open:
                s.sl = s.entry
                self._save()
        if s.symbol in self.open and s.tp1_done and tp2_hit:
            await self._close_part(s, s.remaining, s.tp2, "TP2")

    async def _close_part(self, s: TrackedSignal, share: float, price: float, reason: str,
                          moved_be: bool = False) -> None:
        sign = 1 if s.side == "LONG" else -1
        r = (price - s.entry) * sign / s.risk if s.risk else 0.0
        s.result_r += r * share
        s.remaining -= share
        self.journal.write("result", symbol=s.symbol, side=s.side, reason=reason, price=price,
                           share=round(share, 3), r=round(r, 2))
        await self.tg.send(exit_message(s, reason, share, price, r, moved_be))
        if s.remaining <= 1e-9:
            self.open.pop(s.symbol, None)
            self.journal.write("signal_closed", symbol=s.symbol, side=s.side, total_r=round(s.result_r, 2))
            await self.tg.send(closed_message(s))
        self._save()
