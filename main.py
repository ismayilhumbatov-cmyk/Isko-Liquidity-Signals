"""
Liquidity Sweep / Hunt SIGNAL bot for Binance USDT-M perpetual futures.

The bot never trades. It reads public Binance market data, finds liquidity sweep
setups and sends them to Telegram (🟢 LONG / 🔴 SHORT), then reports TP1, TP2,
stop loss or breakeven for each signal.

Run:  python main.py
All settings are in .env (see .env.example and README.md).
"""
import asyncio
import logging
import signal
import time

import ccxt.async_support as ccxt

from bot.config import Config
from bot.heatmap import CoinglassHeatmap
from bot.market_data import MarketFeed
from bot.notifier import Telegram, armed_message
from bot.state import SymbolState
from bot.strategy import Strategy
from bot.symbols import select_symbols
from bot.tracker import SignalTracker
from bot.utils import Journal, fmt_usd, now_ms, setup_logging
from bot.zones import build_zones

log = logging.getLogger("main")


class Bot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
        self.journal = Journal()
        # public market data only: no API keys, no orders
        self.ex = ccxt.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.heatmap = CoinglassHeatmap(cfg.coinglass_api_key)
        self.strategy = Strategy(cfg)
        self.tracker = SignalTracker(cfg, self.tg, self.journal)
        self.states: dict[str, SymbolState] = {}
        self.feed = None
        self.feed_task: asyncio.Task | None = None

    async def rebuild_zones(self, st: SymbolState) -> None:
        """Rule 1: refresh the 4H liquidity map of one pair."""
        st.zones, st.atr_4h = build_zones(st.candles_4h, now_ms(), self.cfg)
        if self.heatmap.enabled and st.zones:
            CoinglassHeatmap.boost(st.zones, await self.heatmap.levels(st.symbol), st.atr_4h)
        log.info("%s: %d zones | %s", st.symbol, len(st.zones),
                 " | ".join(z.describe() for z in st.zones[:4]) or "none")

    async def zone_refresher(self) -> None:
        """Safety refresh every 30 minutes (each 4H close also rebuilds instantly)."""
        while True:
            await asyncio.sleep(1800)
            for st in list(self.states.values()):
                await self.rebuild_zones(st)

    async def pair_refresher(self) -> None:
        """Every PAIR_REFRESH_MIN: add coins that now have enough volume, drop coins that lost it."""
        while True:
            await asyncio.sleep(self.cfg.pair_refresh_min * 60)
            try:
                fresh = await select_symbols(self.ex, self.cfg)
            except Exception as e:
                log.warning("pair refresh failed: %s", e)
                continue
            for pid, (_, vol) in fresh.items():          # update 24h volume of known pairs
                if pid in self.states:
                    self.states[pid].volume_24h = vol
            added = [pid for pid in fresh if pid not in self.states]
            removed = [pid for pid in self.states if pid not in fresh and not self.tracker.has_open(pid)]
            if not added and not removed:
                continue
            for pid in removed:
                self.states.pop(pid, None)
            new_states = []
            for pid in added:
                sym, vol = fresh[pid]
                st = SymbolState(pid, sym, volume_24h=vol)
                self.states[pid] = st
                new_states.append(st)
            await self.feed.bootstrap(new_states)
            for st in new_states:
                await self.rebuild_zones(st)
            self._restart_feed()                         # live streams for the new pair list
            log.info("Pairs updated: +%d %s | -%d %s | now %d", len(added), ",".join(added),
                     len(removed), ",".join(removed), len(self.states))

    def _restart_feed(self) -> None:
        if self.feed_task and not self.feed_task.done():
            self.feed_task.cancel()
        self.feed_task = asyncio.create_task(self.feed.run())
        self.feed_task.add_done_callback(self._feed_stopped)

    def _feed_stopped(self, task: asyncio.Task) -> None:
        """If the data feed ever crashes, log it and start it again after 10 seconds."""
        if task.cancelled():
            return
        log.error("Data feed stopped: %s - restarting in 10s", task.exception())
        asyncio.get_running_loop().call_later(10, self._restart_feed)

    async def evaluator(self) -> None:
        """Every EVAL_INTERVAL_SEC: follow open signals, then look for new setups."""
        while True:
            now = now_ms()
            for st in list(self.states.values()):
                if not st.updated:
                    continue
                st.updated = False
                try:
                    await self.tracker.on_tick(st)
                    st.tick_high = st.tick_low = None
                    if self.tracker.has_open(st.symbol):
                        st.armed_long = None
                        continue
                    was_armed = st.armed_long is not None
                    sig = self.strategy.evaluate(st, now)
                    if st.armed_long and not was_armed:
                        self.journal.write("long_armed", symbol=st.symbol, zone=st.armed_long["zone"].describe(),
                                           rsi=round(st.armed_long["rsi_min"], 1))
                        if self.cfg.notify_armed:
                            await self.tg.send(armed_message(st.symbol, st.armed_long))
                    if sig:
                        st.cooldown_until = time.time() + self.cfg.cooldown_min * 60
                        await self.tracker.publish(sig, st.volume_24h)
                except Exception:
                    log.exception("evaluation error on %s", st.symbol)
            await asyncio.sleep(self.cfg.eval_interval_sec)

    async def run(self) -> None:
        cfg = self.cfg
        pairs = await select_symbols(self.ex, cfg)
        if not pairs:
            raise SystemExit("No coins pass the volume filter - check MIN_24H_VOLUME_USDT / PAIRS")
        self.states = {pid: SymbolState(pid, sym, volume_24h=vol) for pid, (sym, vol) in pairs.items()}
        log.info("Loading history for %d pairs (takes a few minutes with many coins)...", len(pairs))

        self.feed = MarketFeed(cfg, self.ex, self.states, self.rebuild_zones)
        await self.feed.bootstrap()
        for st in self.states.values():
            await self.rebuild_zones(st)

        await self.tg.send(
            f"🤖 <b>Liquidity Sweep signal bot started</b>\n"
            f"Scanning {len(pairs)} coins with 24h volume ≥ {fmt_usd(cfg.min_24h_volume)} USDT\n"
            f"Coin list refreshes every {cfg.pair_refresh_min:g} min\n"
            f"🟢 = LONG   🔴 = SHORT   (signals only, no trading)"
        )
        self.journal.write("start", pairs=list(pairs))
        self._restart_feed()
        await asyncio.gather(self.evaluator(), self.zone_refresher(), self.pair_refresher())

    async def close(self) -> None:
        for closer in (self.ex.close, self.tg.close, self.heatmap.close):
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
        except NotImplementedError:  # Windows
            pass
    try:
        await bot.run()
    except asyncio.CancelledError:
        log.info("Stopping...")
    finally:
        await bot.tg.send("⏹ Liquidity Sweep signal bot stopped.")
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
