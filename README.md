# Trend + EMA 50 Retest Signal Bot — Binance USDT-M Futures

**Signals only.** The bot never places orders and needs no Binance API keys. It reads public Binance Futures data, sends 🟢 LONG / 🔴 SHORT signals to Telegram, and then reports ✅ TP1, 🎯 TP2, 🛑 stop loss or ⚖️ breakeven for each signal.

## The strategy

**1. 4H trend decides the direction.**
Price above a rising 4H EMA 50 means uptrend, so LONG only. Price below a falling 4H EMA 50 means downtrend, so SHORT only. No clear trend means no trade on that coin.

**2. 15m confirms it.**
For LONG, the last 15m candle must close above the 15m EMA 50. For SHORT, below it.

**3. 1m gives the entry, all within 15 minutes:**
- **Cross:** a 1m candle closes through the EMA 50 in the trend direction.
- **Retest:** a later candle comes back and touches the EMA 50 without closing through it. It can be the very next candle.
- **Big candle:** a separate candle after the retest closes strongly in the trend direction. Its body is at least 1.5× the average and its volume is above average. It closes beyond the EMA 50 and beyond the retest candle. **Entry is at its close.**

The setup is cancelled if price closes back through the EMA 50, if 15 minutes pass, or if the 4H or 15m direction changes.

**4. Stop and targets.**
The stop sits just beyond the retest wick, never closer than 0.3% from entry. TP1 at 1.5R closes half and moves the stop to entry. TP2 at 3R closes the rest. (R = distance from entry to stop.)

## Coins

All Binance USDT-M perpetual crypto coins with at least 3 million USDT futures volume in 24 hours. Stablecoins, gold and stock contracts are excluded. The list refreshes every 60 minutes.

## Install or update (Hetzner black console, no Shift keys needed)

```
curl --proto-default https raw.githubusercontent.com/ismayilhumbatov-cmyk/isko-liquidity-signals/main/install.sh -o i.sh
bash i.sh
```

## Change settings

Open `settings.env` on GitHub, click the pencil icon, change a number, commit, then run the two install lines once. Each setting is explained in the file.

## Telegram messages

- **🤖 started / ⏹ stopped:** the bot starts or stops (normal during updates)
- **🟢 LONG / 🔴 SHORT:** a signal, with entry, stop loss, TP1, TP2 and the reasons
- **✅ 🎯 🛑 ⚖️:** results of each signal
- **📊 Status** every 6 hours: trend count, crosses, retests, signals
- **⚠️ Bot error:** the bot hit an error and restarts by itself
