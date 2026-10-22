"""
OPTIONAL - liquidity heatmap confirmation (Coinglass).

There is no free public liquidation-heatmap API. Coinglass offers one on paid
API plans. If COINGLASS_API_KEY is empty (default) this module does nothing and
the bot works only from the 4H price structure.

If a key is set, zones that contain a strong heatmap cluster get +1 strength and
the label "liquidity heatmap". The endpoint and response format below follow the
Coinglass v4 docs at the time of writing - check them against your plan's docs.
Any error is logged and ignored, so the bot never stops because of this module.
"""
import logging
import time

import aiohttp

log = logging.getLogger("heatmap")

URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/heatmap/model1"


class CoinglassHeatmap:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.cache: dict = {}
        self.session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def close(self):
        if self.session:
            await self.session.close()

    async def levels(self, symbol: str) -> list[tuple[float, float]]:
        """Returns [(price, liquidation_intensity), ...] or [] on any problem."""
        if not self.enabled:
            return []
        cached = self.cache.get(symbol)
        if cached and time.time() - cached[0] < 900:
            return cached[1]
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            params = {"exchange": "Binance", "symbol": symbol, "range": "3d"}
            headers = {"CG-API-KEY": self.api_key, "accept": "application/json"}
            async with self.session.get(URL, params=params, headers=headers) as r:
                js = await r.json(content_type=None)
            data = js.get("data") or {}
            prices = data.get("y_axis") or []
            points = data.get("liquidation_leverage_data") or []
            totals: dict = {}
            for pt in points:               # each point: [x_index, y_index, value]
                if len(pt) >= 3:
                    totals[int(pt[1])] = totals.get(int(pt[1]), 0.0) + float(pt[2])
            result = [(float(prices[i]), v) for i, v in totals.items() if i < len(prices)]
            self.cache[symbol] = (time.time(), result)
            return result
        except Exception as e:  # never break the bot because of optional data
            log.warning("heatmap %s unavailable: %s", symbol, e)
            return []

    @staticmethod
    def boost(zones: list, levels: list, atr_4h: float) -> None:
        """+1 strength for zones that contain one of the strongest 20% heatmap clusters."""
        if not levels or not zones:
            return
        values = sorted(v for _, v in levels)
        cutoff = values[int(len(values) * 0.8)]
        strong = [p for p, v in levels if v >= cutoff]
        pad = 0.25 * atr_4h
        for z in zones:
            if any(z.bottom - pad <= p <= z.top + pad for p in strong):
                z.strength += 1
                z.sources.append("liquidity heatmap")
