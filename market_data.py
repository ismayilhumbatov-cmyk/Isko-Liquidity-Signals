"""
Market data.

* Start-up: loads 300 x 4H and 300 x 5m candles per pair over REST (enough history
  for RSI to match the chart and to map 4H zones).
* Live: Binance WebSocket kline streams (5m + 4H) for every pair.
* Safety net: if the WebSocket keeps failing (network rules, URL change), the bot
  switches automatically to REST polling every REST_POLL_SEC seconds.
"""
import asyncio
import json
import logging
import time

from .state import SymbolState
from .utils import C, H, L, T, TF_MS, now_ms

log = logging.getLogger("data")

MAINNET_WS = "wss://fstream.binance.com"
STREAMS_PER_CONNECTION = 100


def _push_closed(store, candle: list) -> bool:
    """Add a closed candle (or replace the same one). Returns True if it is new."""
    if store and store[-1][T] == candle[T]:
        store[-1] = candle
        return False
    if not store or candle[T] > store[-1][T]:
        store.append(candle)
        return True
    return False


class MarketFeed:
    def __init__(self, cfg, exchange, states: dict, on_4h_close):
        self.cfg = cfg
        self.ex = exchange
        self.states: dict[str, SymbolState] = states
        self.on_4h_close = on_4h_close        # async callback(state) when a 4H candle closes
        self.use_rest = cfg.data_source == "rest"

    # ------------------------------------------------------------ REST helpers
    async def _fetch(self, st: SymbolState, tf: str, limit: int) -> None:
        rows = await self.ex.fetch_ohlcv(st.ccxt_symbol, tf, limit=limit)
        now = now_ms()
        closed = [[int(r[0]), *map(float, r[1:6])] for r in rows if r[0] + TF_MS[tf] <= now]
        live = next(([int(r[0]), *map(float, r[1:6])] for r in rows if r[0] + TF_MS[tf] > now), None)
        if tf == "4h":
            if limit > 10:
                st.candles_4h = closed
            else:
                for c in closed:
                    if _push_closed(st.candles_4h, c):
                        await self.on_4h_close(st)
            st.live_4h = live
        else:
            if limit > 10:
                st.candles_5m.clear()
                st.candles_5m.extend(closed)
            else:
                for c in closed:
                    _push_closed(st.candles_5m, c)
            if live:
                st.live_5m = live
                st.last_price = live[C]
                if limit <= 10:
                    # REST mode: only the current price is known for exit checks
                    st.tick_high = max(st.tick_high or live[C], live[C])
                    st.tick_low = min(st.tick_low or live[C], live[C])
        st.updated = True

    async def bootstrap(self, only: list | None = None) -> None:
        """Load history for all pairs (or only the given new pairs)."""
        targets = only if only is not None else list(self.states.values())
        for st in targets:
            for attempt in range(3):
                try:
                    await self._fetch(st, "4h", self.cfg.htf_candles)
                    await self._fetch(st, "5m", 300)
                    break
                except Exception as e:
                    log.warning("bootstrap %s failed (%s), retrying", st.symbol, e)
                    await asyncio.sleep(2)
        log.info("History loaded for %d pairs", len(targets))

    async def _resync_recent(self) -> None:
        """After a reconnect, fill any candles missed while disconnected."""
        for st in list(self.states.values()):
            try:
                await self._fetch(st, "5m", 6)
                await self._fetch(st, "4h", 3)
            except Exception as e:
                log.warning("resync %s failed: %s", st.symbol, e)

    # ------------------------------------------------------------- WebSocket
    def _apply_kline(self, k: dict) -> None:
        st = self.states.get(k["s"])
        if st is None:
            return
        candle = [int(k["t"]), float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"])]
        closed = bool(k["x"])
        if k["i"] == "5m":
            price = candle[C]
            st.last_price = price
            st.tick_high = price if st.tick_high is None else max(st.tick_high, price)
            st.tick_low = price if st.tick_low is None else min(st.tick_low, price)
            if closed:
                _push_closed(st.candles_5m, candle)
                st.live_5m = None
            else:
                st.live_5m = candle
            st.updated = True
        elif k["i"] == "4h":
            if closed:
                if _push_closed(st.candles_4h, candle):
                    del st.candles_4h[:-self.cfg.htf_candles]
                    asyncio.create_task(self.on_4h_close(st))
                st.live_4h = None
            else:
                st.live_4h = candle

    async def _ws_worker(self, symbols: list[str]) -> None:
        import websockets

        base = self.cfg.ws_url or MAINNET_WS
        streams = "/".join(f"{s.lower()}@kline_{tf}" for s in symbols for tf in ("5m", "4h"))
        url = f"{base}/stream?streams={streams}"
        failures, first = 0, True
        while not self.use_rest:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 22) as ws:
                    log.info("WebSocket connected (%d pairs)", len(symbols))
                    if not first:
                        await self._resync_recent()
                    first, failures = False, 0
                    async for raw in ws:
                        if self.use_rest:      # another connection switched the bot to REST
                            return
                        msg = json.loads(raw)
                        data = msg.get("data", msg)
                        if data.get("e") == "kline":
                            self._apply_kline(data["k"])
            except Exception as e:
                failures += 1
                log.warning("WebSocket error (%d): %s", failures, e)
                if failures >= 5:
                    log.error("WebSocket keeps failing -> switching to REST polling")
                    self.use_rest = True
                    return
                await asyncio.sleep(min(30, 2 ** failures))

    async def _rest_loop(self) -> None:
        log.info("REST polling every %ss", self.cfg.rest_poll_sec)
        last_4h = 0.0
        while True:
            started = time.time()
            refresh_4h = started - last_4h > 300
            for st in list(self.states.values()):
                try:
                    await self._fetch(st, "5m", 3)
                    if refresh_4h:
                        await self._fetch(st, "4h", 3)
                except Exception as e:
                    log.warning("REST poll %s: %s", st.symbol, e)
            if refresh_4h:
                last_4h = started
            # with many pairs, poll a bit slower to stay inside Binance rate limits
            interval = max(self.cfg.rest_poll_sec, len(self.states) * 0.06)
            await asyncio.sleep(max(1.0, interval - (time.time() - started)))

    async def run(self) -> None:
        if not self.use_rest:
            ids = list(self.states)
            per_conn = STREAMS_PER_CONNECTION // 2
            chunks = [ids[i:i + per_conn] for i in range(0, len(ids), per_conn)]
            await asyncio.gather(*(self._ws_worker(c) for c in chunks))
        await self._rest_loop()
