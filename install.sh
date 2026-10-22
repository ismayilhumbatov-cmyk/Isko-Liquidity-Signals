#!/usr/bin/env bash
# One-command installer / updater for the Liquidity Sweep signal bot.
# Run on the server as root:
#   curl -sL https://raw.githubusercontent.com/ismayilhumbatov-cmyk/Isko-Liquidity-Signals/main/install.sh | bash
set -e

REPO="https://github.com/ismayilhumbatov-cmyk/Isko-Liquidity-Signals.git"
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
  git -C "$DIR" pull -q
else
  rm -rf "$DIR"
  echo "Downloading the bot..."
  git clone -q "$REPO" "$DIR"
fi
cd "$DIR"

# 4) Python libraries
echo "Installing libraries..."
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 5) Telegram settings (Enter = keep the value already saved)
[ -f .env ] || cp .env.example .env
OLD_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" .env | tail -1 | cut -d= -f2-)
OLD_CHAT=$(grep -E "^TELEGRAM_CHAT_ID=" .env | tail -1 | cut -d= -f2-)
echo ""
if [ -n "$OLD_TOKEN" ]; then
  read -rp "Telegram bot token (press Enter to keep the saved one): " TOKEN </dev/tty
else
  read -rp "Paste your Telegram bot token and press Enter: " TOKEN </dev/tty
fi
if [ -n "$OLD_CHAT" ]; then
  read -rp "Telegram chat ID (press Enter to keep the saved one): " CHAT </dev/tty
else
  read -rp "Paste your Telegram chat ID and press Enter: " CHAT </dev/tty
fi
TOKEN=$(echo "${TOKEN:-$OLD_TOKEN}" | tr -d '[:space:]')
CHAT=$(echo "${CHAT:-$OLD_CHAT}" | tr -d '[:space:]')
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "Token and chat ID are both needed. Run the command again."
  exit 1
fi
sed -i "/^TELEGRAM_BOT_TOKEN=/d; /^TELEGRAM_CHAT_ID=/d" .env
echo "TELEGRAM_BOT_TOKEN=$TOKEN" >> .env
echo "TELEGRAM_CHAT_ID=$CHAT" >> .env

# 6) Run as a service (starts now and after every reboot)
cp deploy/liquidity-bot.service /etc/systemd/system/liquidity-bot.service
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
