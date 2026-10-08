#!/usr/bin/env bash
# Включает автозапуск бота и Cloudflare-туннеля после перезагрузки VPS.
# Запуск: bash enable_autostart.sh

set -euo pipefail

REPO_DIR="/root/mine"
BRANCH="cursor/mineslot-telegram-bot-d3fb"

if [ ! -d "$REPO_DIR" ]; then
  echo "Папка $REPO_DIR не найдена. Сначала установите бота."
  exit 1
fi

cd "$REPO_DIR"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

if [ ! -x /usr/local/bin/cloudflared ]; then
  echo "cloudflared не найден. Ставлю..."
  ARCH=$(uname -m)
  case "$ARCH" in
    aarch64|arm64) CF_ARCH="arm64" ;;
    *) CF_ARCH="amd64" ;;
  esac
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
    -o /usr/local/bin/cloudflared
  chmod +x /usr/local/bin/cloudflared
fi

# Останавливаем старые ручные процессы
pkill -f "python bot.py" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

cp "$REPO_DIR/mineslot-bot.service" /etc/systemd/system/mineslot-bot.service
cp "$REPO_DIR/mineslot-tunnel.service" /etc/systemd/system/mineslot-tunnel.service

systemctl daemon-reload
systemctl enable mineslot-bot.service mineslot-tunnel.service
systemctl restart mineslot-bot.service
sleep 2
systemctl restart mineslot-tunnel.service
sleep 5

echo ""
echo "Статус бота:"
systemctl --no-pager --full status mineslot-bot.service | head -15 || true
echo ""
echo "Статус туннеля:"
systemctl --no-pager --full status mineslot-tunnel.service | head -15 || true
echo ""

TUNNEL_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' /root/mine/cloudflared.log | tail -1 || true)
if [ -n "$TUNNEL_URL" ]; then
  echo "Cloudflare URL: $TUNNEL_URL"
  echo "В 1win (Регистрация): ${TUNNEL_URL}/postback?sub1={sub1}"
  echo "${TUNNEL_URL}/postback?sub1={sub1}" > /root/mine/POSTBACK_FOR_1WIN.txt
  # обновим PUBLIC_BASE_URL если есть .env
  if [ -f /root/mine/.env ]; then
    sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${TUNNEL_URL}|" /root/mine/.env
    systemctl restart mineslot-bot.service
  fi
else
  echo "URL туннеля пока не найден. Смотрите: journalctl -u mineslot-tunnel -n 50"
fi

echo ""
echo "Автозапуск включён. После перезагрузки сервера бот поднимется сам."
echo "Проверка: systemctl status mineslot-bot"
