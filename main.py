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

    # ----------------------------------------------------------- status
    async def status_reporter(self) -> None:
        if self.cfg.status_hours <= 0:
            return
        while True:
            await asyncio.sleep(self.cfg.status_hours * 3600)
            try:
                await self._send_status()
            except Exception:
                log.exception("status report failed")

    async def _send_status(self) -> None:
        up = down = flat = 0
        for st in list(self.states.values()):
            t = self.strategy.trend_4h(st)
            up += t == "UP"
            down += t == "DOWN"
            flat += t is None
        s = self.strategy.stats
        data = "REST polling (slower)" if self.feed and self.feed.use_rest else "live"
        await self.tg.send(
            f"📊 <b>Status — last {self.cfg.status_hours:g}h</b>\n"
            f"Coins scanned: {len(self.states)}  (data: {data})\n"
            f"4H trend now: {up} up • {down} down • {flat} no trend\n"
            f"1m EMA 50 crosses with trend: {s['crosses']}\n"
            f"Retests: {s['retests']}\n"
            f"Not confirmed in {self.cfg.retest_window_min:g} min: {s['expired']}\n"
            f"Failed (closed back through EMA): {s['failed']}\n"
            f"Signals sent: {s['signals']}  •  Open: {len(self.tracker.open)}"
        )
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
        await asyncio.gather(self.evaluator(), self.pair_refresher(), self.status_reporter())

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
