# Liquidity Sweep Signal Bot — Binance USDT-M Futures

**Signals only. The bot never places orders and needs no Binance API keys.**
It reads public Binance market data, finds liquidity sweep (stop hunt) setups and sends them to Telegram. It then follows each signal and reports the result.

🟢 = LONG  🔴 = SHORT  ✅ = TP1 hit  🎯 = TP2 hit  🛑 = Stop loss  ⚖️ = Breakeven

## Which coins are scanned

The bot scans **every** Binance USDT perpetual crypto coin, not a fixed list. Only coins with real trading activity are used: at least 20 million USDT traded in the last 24 hours (`MIN_24H_VOLUME_USDT`). Quiet coins are skipped, because a "sweep" on a thin order book is usually just one big order, not a real stop hunt. Stablecoin pairs and non-crypto contracts (gold, stocks) are always removed.

The coin list is refreshed every 60 minutes: coins that become active are added, coins that go quiet are removed (unless they have an open signal). Every signal shows the coin's 24h volume.

On a normal day this is roughly 100–250 coins. The first start takes a few minutes while the bot loads the history of each coin. To scan more coins, lower `MIN_24H_VOLUME_USDT` (for example 10000000). To scan fewer and only the most active, raise it or set `MAX_PAIRS`.

## How the strategy works

**Step 1 — 4H liquidity map.** The bot reads the last 300 four-hour candles of each pair and marks where many stop orders are likely sitting: swing highs and lows, equal highs and lows, previous day and previous week high and low, and strong order blocks. A zone is kept only if price has not already traded through it (the stops are still there). Each zone gets a strength score, and weak zones are ignored. The map is rebuilt at every 4H close and every 30 minutes.

**Step 2 — 5-minute signal, only inside a 4H zone.**

🔴 SHORT (sweep above resistance): a big green 5m candle pushes into the resistance zone, 5m RSI is above 80, price is in the top part of that candle and volume is rising. The signal is sent right away, before the candle finishes.

🟢 LONG (sweep below support): a big red 5m candle pushes into the support zone and RSI falls to 30 or lower (25 or lower is stronger). The bot waits for the first green 5m candle that closes back above the zone floor with buyers' volume and RSI turning up, then sends the signal. If that does not happen within 30 minutes, or price closes far below the zone, the setup is dropped.

**Step 3 — volume check.** No signal if the 4H candle shows a volume spike above 2.5× normal, because that usually means the zone was already cleared.

**Step 4 — stop and targets in the signal.** The stop is just beyond the sweep candle's extreme, or just beyond the zone if that is closer, and never closer than 0.3% from entry. TP1 (close 50%) is the next opposite 4H zone, or 2R. TP2 (the rest) is the zone after that, or 3R. After TP1 the bot tells you to move the stop to entry.

"R" means the distance from entry to stop. A 2R target is twice the size of the possible loss.

## What you receive in Telegram

1. The signal: side, entry, stop loss, TP1, TP2, leverage, risk and the reasons.
2. Updates for that signal: TP1 hit, TP2 hit, stop loss or breakeven.
3. A final line with the total result of the signal in R.

Only one signal per pair is open at a time, and no new signal comes on the same pair within 30 minutes.

## Folder structure

```
liquidity-sweep-bot/
├── main.py              start here
├── requirements.txt
├── .env.example         copy to .env and fill in
├── deploy/liquidity-bot.service   systemd file for the Hetzner server
└── bot/
    ├── config.py        all settings
    ├── zones.py         Step 1: 4H liquidity zones
    ├── strategy.py      Steps 2-4: 5m signal rules, stop, targets
    ├── tracker.py       follows each signal to TP / SL and reports it
    ├── market_data.py   live candles (WebSocket, automatic REST fallback)
    ├── indicators.py    RSI and ATR (same method as TradingView)
    ├── symbols.py       all-coin scan, volume filter, removes stablecoins
    ├── notifier.py      Telegram messages
    ├── heatmap.py       optional Coinglass heatmap (paid API key)
    ├── state.py         live data per pair
    └── utils.py         logging and signal journal
```

## Install on the Hetzner server

First stop the old bot so only one bot writes to your Telegram chat. Then:

```bash
cd /opt
git clone https://github.com/<your-user>/Isko-Futures-Signals.git liquidity-sweep-bot
cd liquidity-sweep-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env            # add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
.venv/bin/python main.py      # first run, stop with Ctrl+C
```

Keep it running all the time (restarts by itself after a crash or reboot):

```bash
sudo cp deploy/liquidity-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now liquidity-bot
journalctl -u liquidity-bot -f        # watch the logs
```

If the folder is not `/opt/liquidity-sweep-bot`, change `WorkingDirectory` and `ExecStart` in the service file.

To update after you push new code to GitHub: `git pull && sudo systemctl restart liquidity-bot`.

The Telegram token goes only in `.env` on the server. The repo is public, and `.env` is already excluded from GitHub by `.gitignore`.

## Main settings (.env)

| Setting | Default | Meaning |
|---|---|---|
| `SCAN_ALL` | true | Scan all Binance USDT perpetual crypto coins. `false` = only the coins in `PAIRS`. |
| `MIN_24H_VOLUME_USDT` | 20000000 | Minimum 24h traded value for a coin to be scanned and signalled. |
| `PAIR_REFRESH_MIN` | 60 | How often the coin list is refreshed. |
| `MAX_PAIRS` | 0 | 0 = no limit. Otherwise only the N most active coins. |
| `RSI_SHORT_LEVEL` / `RSI_LONG_LEVEL` | 80 / 30 | RSI trigger levels. |
| `BIG_CANDLE_ATR` | 1.5 | A "big" candle is at least 1.5× the normal 5m candle size. |
| `VOL_EXPANSION_MULT` | 1.5 | Sweep candle volume must be at least 1.5× the recent average. |
| `TP1_R` / `TP2_R` | 2 / 3 | Fixed targets when no liquidity zone is in range. |
| `MIN_SL_PCT` | 0.3 | Stop is never closer than 0.3% from entry. |
| `LEVERAGE` / `RISK_PCT` | 10 / 1 | Only shown in the message as a suggestion. |
| `COOLDOWN_MINUTES` | 30 | No second signal on the same pair within this time. |
| `NOTIFY_ARMED` | false | Also message when a LONG sweep is being watched, before the signal. |

All other settings are explained in `.env.example`. Restart the bot after changing `.env`.

## Logs

* `logs/bot.log` — everything the bot does, including why setups were skipped.
* `logs/journal.jsonl` — one line per signal and result, with the reasons. Useful for checking the win rate later.
* `state/signals.json` — open signals, so a restart does not lose them.
