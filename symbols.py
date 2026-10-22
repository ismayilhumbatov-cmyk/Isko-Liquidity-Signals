"""
Pair selection.

* Scans every Binance USDT-margined PERPETUAL crypto contract (SCAN_ALL=true),
  or only the coins in PAIRS (SCAN_ALL=false).
* Only coins with real trading activity are used: 24h traded value must be at
  least MIN_24H_VOLUME_USDT (default 20 million USDT). Low-volume coins are skipped,
  because their "sweeps" are often just thin order books, not real stop hunts.
* The list is refreshed every PAIR_REFRESH_MIN minutes: coins that gain volume are
  added, coins that lose it are removed.
* Stablecoin pairs (USDCUSDT, FDUSDUSDT, TUSDUSDT ...) and non-crypto contracts
  (gold, stocks and other TradFi perpetuals) are always removed.
"""
import logging

log = logging.getLogger("symbols")

STABLECOINS = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PAX", "USDD", "PYUSD", "USDE",
    "USDS", "USD1", "RLUSD", "SUSD", "GUSD", "LUSD", "FRAX", "UST", "USTC", "EUR", "EURI",
    "AEUR", "EURC", "XUSD", "BFUSD", "USDX", "USDB", "USD0", "USDF", "USDG",
}


def is_stablecoin(base: str) -> bool:
    b = base.upper()
    return b in STABLECOINS or (b.startswith("USD") and len(b) <= 6)


def _allowed(market: dict) -> bool:
    info = market.get("info", {}) or {}
    return bool(
        market.get("swap")
        and market.get("linear")
        and market.get("settle") == "USDT"
        and market.get("quote") == "USDT"
        and market.get("active", True)
        and info.get("contractType", "PERPETUAL") == "PERPETUAL"
        and info.get("underlyingType", "COIN") == "COIN"
        and info.get("status", "TRADING") == "TRADING"
        and not is_stablecoin(market.get("base", ""))
    )


async def select_symbols(exchange, cfg) -> dict:
    """Returns {binance_id: (ccxt_symbol, volume_24h_usdt)}, highest volume first."""
    markets = await exchange.load_markets(reload=True)
    allowed = {m["id"]: m["symbol"] for m in markets.values() if _allowed(m)}
    excluded = set(cfg.exclude_pairs)
    candidates = allowed if cfg.scan_all else {p: allowed[p] for p in cfg.pairs if p in allowed}
    for p in cfg.pairs:
        if not cfg.scan_all and p not in allowed:
            log.warning("Pair %s skipped (stablecoin, not a crypto perpetual, or not on Binance futures)", p)

    tickers = await exchange.fetch_tickers()
    ranked = []
    for pid, sym in candidates.items():
        if pid in excluded:
            continue
        volume = float((tickers.get(sym) or {}).get("quoteVolume") or 0)
        if volume >= cfg.min_24h_volume:
            ranked.append((volume, pid, sym))
    ranked.sort(reverse=True)
    if cfg.max_pairs > 0:
        ranked = ranked[: cfg.max_pairs]

    result = {pid: (sym, vol) for vol, pid, sym in ranked}
    log.info("%d of %d coins have 24h volume >= %s USDT", len(result), len(candidates),
             f"{cfg.min_24h_volume:,.0f}")
    return result
