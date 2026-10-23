"""
Trend + EMA 50 Retest SIGNAL bot for Binance USDT-M perpetual futures.

The bot never trades. It reads public Binance Futures data and sends signals to Telegram:
  4H trend  ->  15m check  ->  1m EMA 50 cross, retest, big candle   (🟢 LONG / 🔴 SHORT)
then reports TP1, TP2, stop loss or breakeven for every signal.

Run:  python main.py      (settings: settings.env from GitHub, Telegram details: .env)
"""
import asyncio
import html
import logging
import signal
import time

import ccxt.async_support as ccxt

from bot.config import Config
from bot.market_data import MarketFeed
from bot.notifier import Telegram
from bot.state import SymbolState
from bot.strategy import Strategy
from bot.symbols import select_symbols
from bot.tracker import SignalTracker
from bot.utils import Journal, fmt_usd, now_ms, setup_logging

log = logging.getLogger("main")
BOT_NAME = "Trend + EMA 50 Retest signal bot"


class Bot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
        self.journal = Journal()
        self.ex = ccxt.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.strategy = Strategy(cfg)
        self.tracker = SignalTracker(cfg, self.tg, self.journal)
        self.states: dict[str, SymbolState] = {}
        self.feed: MarketFeed | None = None
        self.feed_task: asyncio.Task | None = None

    # ------------------------------------------------------------ coins
    async def pair_refresher(self) -> None:
        """Every PAIR_REFRESH_MIN: add coins that now have enough volume, drop coins that lost it."""
        while True:
            await asyncio.sleep(self.cfg.pair_refresh_min * 60)
            try:
                await self._refresh_pairs()
            except Exception:
                log.exception("coin list refresh failed")

    async def _refresh_pairs(self) -> None:
        fresh = await select_symbols(self.ex, self.cfg)
        for pid, (_, vol) in fresh.items():
            if pid in self.states:
                self.states[pid].volume_24h = vol
        added = [pid for pid in fresh if pid not in self.states]
        removed = [pid for pid in self.states if pid not in fresh and not self.tracker.has_open(pid)]
        if not added and not removed:
            return
        for pid in removed:
            self.states.pop(pid, None)
        new_states = []
        for pid in added:
            sym, vol = fresh[pid]
            st = SymbolState(pid, sym, volume_24h=vol)
            self.states[pid] = st
            new_states.append(st)
        await self.feed.bootstrap(new_states)
        self._restart_feed()
        log.info("Coins updated: +%d %s | -%d %s | now %d", len(added), ",".join(added),
                 len(removed), ",".join(removed), len(self.states))

    # ------------------------------------------------------------- feed
    def _restart_feed(self) -> None:
        if self.feed_task and not self.feed_task.done():
            self.feed_task.cancel()
        self.feed_task = asyncio.create_task(self.feed.run())
        self.feed_task.add_done_callback(self._feed_stopped)

    def _feed_stopped(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        log.error("Data feed stopped: %s - restarting in 10s", task.exception())
        asyncio.get_running_loop().call_later(10, self._restart_feed)

    # --------------------------------------------------------- watchdog
    def newest_1m_age_min(self) -> float:
        newest = max((st.candles["1m"][-1][0] for st in self.states.values() if st.candles["1m"]), default=0)
        # a 1m candle closes 1 minute after it opens
        return (now_ms() - newest - 60_000) / 60_000 if newest else 999.0

    async def data_watchdog(self) -> None:
        """If no fresh 1m candle arrives for 3 minutes, switch to REST polling and tell the user."""
        await asyncio.sleep(180)
        warned = False
        while True:
            await asyncio.sleep(60)
            try:
                age = self.newest_1m_age_min()
                if age > 3 and self.feed and not self.feed.use_rest:
                    log.warning("No fresh 1m candles for %.0f min -> REST polling", age)
                    self.feed.use_rest = True
                    self._restart_feed()
                    await self.tg.send("⚠️ Live data stopped. Switched to direct price checks every ~15 seconds. "
                                       "Signals keep coming, a few seconds later than live.")
                elif age > 5 and self.feed and self.feed.use_rest and not warned:
                    warned = True
                    await self.tg.send(f"⚠️ No fresh market data for {age:.0f} minutes. Check the server internet.")
                elif age <= 2:
                    warned = False
            except Exception:
                log.exception("data watchdog failed")

    # ----------------------------------------------------------- status
    async def status_reporter(self) -> None:
        """First report 20 minutes after start, then every STATUS_HOURS."""
        if self.cfg.status_hours <= 0:
            return
        await asyncio.sleep(20 * 60)
        while True:
            try:
                await self._send_status()
            except Exception:
                log.exception("status report failed")
            await asyncio.sleep(self.cfg.status_hours * 3600)

    async def _send_status(self) -> None:
        up = down = flat = long_ok = short_ok = 0
        for st in list(self.states.values()):
            t = self.strategy.trend_4h(st)
            up += t == "UP"
            down += t == "DOWN"
            flat += t is None
            side, _ = self.strategy.direction(st)
            long_ok += side == "LONG"
            short_ok += side == "SHORT"
        s = self.strategy.stats
        data = "REST polling (slower)" if self.feed and self.feed.use_rest else "live"
        age = self.newest_1m_age_min()
        text = (
            f"📊 <b>Status</b>\n"
            f"Coins: {len(self.states)}  •  data: {data}  •  newest 1m candle: {max(age, 0):.0f} min old\n"
            f"1m candles checked: {s['candles']}\n"
            f"4H trend: {up} up • {down} down • {flat} no trend\n"
            f"Ready now (4H + 15m agree): {long_ok} LONG • {short_ok} SHORT\n"
            f"Crosses with trend: {s['crosses']}  •  Retests: {s['retests']}\n"
            f"Expired: {s['expired']}  •  Failed: {s['failed']}  •  Weak volume: {s['low_volume']}\n"
            f"Signals sent: {s['signals']}  •  Open: {len(self.tracker.open)}"
        )
        log.info("STATUS %s", text.replace("\n", " | "))
        await self.tg.send(text)
        s.clear()

    # --------------------------------------------------------- strategy
    async def evaluator(self) -> None:
        """Every EVAL_INTERVAL_SEC: follow open signals, then check new closed 1m candles."""
        while True:
            now = now_ms()
            for st in list(self.states.values()):
                if not st.updated:
                    continue
                st.updated = False
                try:
                    await self.tracker.on_tick(st)
                    st.tick_high = st.tick_low = None
                    if self.tracker.has_open(st.symbol) or time.time() < st.cooldown_until:
                        st.setup = None
                        st.last_closed_checked = st.candles["1m"][-1][0] if st.candles["1m"] else 0
                        continue
                    sig = self.strategy.evaluate(st, now)
                    if sig:
                        st.cooldown_until = time.time() + self.cfg.cooldown_min * 60
                        await self.tracker.publish(sig, st.volume_24h)
                except Exception:
                    log.exception("evaluation error on %s", st.symbol)
            await asyncio.sleep(self.cfg.eval_interval_sec)

    # -------------------------------------------------------------- run
    async def run(self) -> None:
        cfg = self.cfg
        pairs = await select_symbols(self.ex, cfg)
        if not pairs:
            raise SystemExit("No coins pass the volume filter - check MIN_24H_VOLUME_USDT")
        self.states = {pid: SymbolState(pid, sym, volume_24h=vol) for pid, (sym, vol) in pairs.items()}
        log.info("Loading history for %d coins (a few minutes)...", len(pairs))
        self.feed = MarketFeed(cfg, self.ex, self.states)
        await self.feed.bootstrap()

        await self.tg.send(
            f"🤖 <b>{BOT_NAME} started</b>\n"
            f"Scanning {len(pairs)} coins with 24h futures volume ≥ {fmt_usd(cfg.min_24h_volume)} USDT\n"
            f"4H trend → {'15m check → ' if cfg.use_15m_filter else ''}1m EMA {cfg.entry_ema} "
            f"cross + retest + big candle (within {cfg.retest_window_min:g} min)\n"
            f"🟢 = LONG   🔴 = SHORT   (signals only, no trading)"
        )
        self.journal.write("start", strategy="ema_retest", pairs=list(pairs))
        self._restart_feed()
        await asyncio.gather(self.evaluator(), self.pair_refresher(), self.status_reporter(),
                             self.data_watchdog())

    async def close(self) -> None:
        for closer in (self.ex.close, self.tg.close):
            try:
                await closer()
            except Exception:
                pass


async def main() -> None:
    cfg = Config()
    setup_logging(cfg.log_level)
    bot = Bot(cfg)
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass
    error = None
    try:
        await bot.run()
    except asyncio.CancelledError:
        log.info("Stopping...")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        log.exception("Bot crashed")
        raise
    finally:
        if error:
            await bot.tg.send(f"⚠️ Bot error, restarting in 10 seconds:\n<code>{html.escape(error[:300])}</code>")
        else:
            await bot.tg.send(f"⏹ {BOT_NAME} stopped.")
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
