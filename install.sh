#!/usr/bin/env bash
# One-command installer / updater for the Liquidity Sweep signal bot.
# Run on the server as root (no Shift keys needed):
#   curl --proto-default https raw.githubusercontent.com/ismayilhumbatov-cmyk/isko-liquidity-signals/main/install.sh -o i.sh
#   bash i.sh GIST_ID
# GIST_ID = id of a secret GitHub gist holding the two Telegram lines (delete the gist afterwards).
set -e

REPO="https://github.com/ismayilhumbatov-cmyk/isko-liquidity-signals.git"
GIST_ID="$1"
DIR="/opt/liquidity-sweep-bot"

echo ""
echo "=== Liquidity Sweep signal bot - installer ==="
if [ "$(id -u)" -ne 0 ]; then
  echo "Please log in as root and run again."
  exit 1
fi

# 1) Offer to stop old bot services so only the new bot writes to Telegram
OLD=$(systemctl list-units --type=service --all --no-legend 2>/dev/null \
      | awk '{print $1}' | grep -i -E "bot|signal" | grep -v "^liquidity-bot.service$" || true)
for s in $OLD; do
  read -rp "Old service found: $s - stop it? (y/n) " a </dev/tty
  if [[ "$a" =~ ^[Yy] ]]; then
    systemctl disable --now "$s" || true
    echo "  stopped $s"
  fi
done

# 2) Tools
echo "Installing tools (1-2 minutes)..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv git >/dev/null

# 3) Code: fresh download, or update if already installed
if [ -d "$DIR/.git" ]; then
  echo "Updating the bot..."
  git -C "$DIR" fetch -q origin
  git -C "$DIR" reset -q --hard origin/HEAD
else
  rm -rf "$DIR"
  echo "Downloading the bot..."
  git clone -q "$REPO" "$DIR"
fi
cd "$DIR"

# If the bot files were uploaded to the main page of the repo instead of a "bot" folder,
# put them into the "bot" folder here (the code expects bot/strategy.py etc.).
if [ -f strategy.py ]; then
  if ! git ls-files --error-unmatch bot/strategy.py >/dev/null 2>&1; then
    rm -rf bot
    mkdir -p bot
    for f in __init__ config heatmap indicators market_data notifier state strategy symbols tracker utils zones; do
      [ -f "$f.py" ] && cp "$f.py" "bot/$f.py"
    done
    [ -f bot/__init__.py ] || touch bot/__init__.py
    echo "Bot files arranged into the bot folder."
  fi
fi

# check that the bot code is really in the GitHub repo
for need in main.py requirements.txt bot/strategy.py; do
  if [ ! -f "$need" ]; then
    echo ""
    echo "ERROR: $need is missing in the GitHub repo."
    echo "Upload the missing files/folders to GitHub (main page of the repo), then run this again."
    exit 1
  fi
done

# 4) Python libraries
echo "Installing libraries..."
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 5) Telegram settings
#    Order: secret gist (if GIST_ID given) -> values already in .env -> old bot's files -> ask.
[ -f .env ] || cp .env.example .env
TOKEN=""; CHAT=""
TOKEN_RE='[0-9]{6,12}:[A-Za-z0-9_-]{30,}'

if [ -n "$GIST_ID" ]; then
  echo "Reading Telegram details from your gist..."
  GIST=$(curl -sL "https://gist.githubusercontent.com/ismayilhumbatov-cmyk/$GIST_ID/raw" || true)
  TOKEN=$(echo "$GIST" | grep -oE "$TOKEN_RE" | head -1)
  CHAT=$(echo "$GIST" | grep -iE "chat" | grep -oE -- "-?[0-9]{5,}" | head -1)
fi
if [ -z "$TOKEN" ]; then
  TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" .env | tail -1 | cut -d= -f2- | tr -d '[:space:]')
fi
if [ -z "$CHAT" ]; then
  CHAT=$(grep -E "^TELEGRAM_CHAT_ID=" .env | tail -1 | cut -d= -f2- | tr -d '[:space:]')
fi
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "Looking for the old bot's Telegram settings on this server..."
  OLDFILES=$(find /opt /root /home /srv /etc/systemd/system -maxdepth 4 -type f \
             \( -name ".env" -o -name "*.service" -o -name "*.env" \) 2>/dev/null | grep -v "$DIR/" || true)
  for f in $OLDFILES; do
    [ -z "$TOKEN" ] && TOKEN=$(grep -oE "$TOKEN_RE" "$f" 2>/dev/null | head -1)
    [ -z "$CHAT" ] && CHAT=$(grep -iE "chat" "$f" 2>/dev/null | grep -oE -- "-?[0-9]{5,}" | head -1)
  done
fi
if [ -z "$TOKEN" ]; then
  read -rp "Telegram bot token: " TOKEN </dev/tty
fi
if [ -z "$CHAT" ]; then
  read -rp "Telegram chat ID: " CHAT </dev/tty
fi
TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')
CHAT=$(echo "$CHAT" | tr -d '[:space:]')
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "Telegram token or chat ID missing. Check your gist and run again."
  exit 1
fi
echo "Telegram token found: ${TOKEN:0:6}...  chat ID: $CHAT"
sed -i "/^TELEGRAM_BOT_TOKEN=/d; /^TELEGRAM_CHAT_ID=/d" .env
echo "TELEGRAM_BOT_TOKEN=$TOKEN" >> .env
echo "TELEGRAM_CHAT_ID=$CHAT" >> .env

# 6) Run as a service (starts now and after every reboot)
cat > /etc/systemd/system/liquidity-bot.service <<SERVICE
[Unit]
Description=Liquidity Sweep Binance Futures signal bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable -q liquidity-bot
systemctl restart liquidity-bot
sleep 3

echo ""
if systemctl is-active --quiet liquidity-bot; then
  echo "DONE - the bot is running."
  echo "It needs 3-5 minutes to load all coins, then you get the start message in Telegram."
else
  echo "The bot did not start. Last log lines:"
  journalctl -u liquidity-bot -n 20 --no-pager
fi
echo ""
echo "Watch the bot:  journalctl -u liquidity-bot -f    (Ctrl+C to leave)"
