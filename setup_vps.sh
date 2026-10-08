#!/usr/bin/env bash
# Автоустановка MineSlot-бота + Cloudflare HTTPS-туннель (без покупки домена)
# Запуск на VPS:  bash setup_vps.sh

set -euo pipefail

REPO_DIR="/root/mine"
BRANCH="cursor/mineslot-telegram-bot-d3fb"
BOT_PORT="8080"
ADMIN_ID_DEFAULT="759878381"
REFERRAL_DEFAULT="https://r1wtvmb.life/casino/list?open=register&p=fiyw"
PUBLIC_IP="194.238.57.246"

echo "==> Обновляем пакеты / ставим зависимости"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git curl ca-certificates

echo "==> Клонируем / обновляем репозиторий"
if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR"
  git fetch origin
  git checkout "$BRANCH"
  git pull origin "$BRANCH"
else
  rm -rf "$REPO_DIR"
  git clone https://github.com/hapak77/mine.git "$REPO_DIR"
  cd "$REPO_DIR"
  git checkout "$BRANCH"
fi

cd "$REPO_DIR"

echo "==> Python venv"
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# Сохраняем токен/секрет из старого .env, если есть
OLD_TOKEN=""
OLD_SECRET=""
OLD_ADMIN=""
if [ -f .env ]; then
  OLD_TOKEN=$(grep -E '^BOT_TOKEN=' .env | head -1 | cut -d= -f2- || true)
  OLD_SECRET=$(grep -E '^POSTBACK_SECRET=' .env | head -1 | cut -d= -f2- || true)
  OLD_ADMIN=$(grep -E '^ADMIN_ID=' .env | head -1 | cut -d= -f2- || true)
fi

BOT_TOKEN="${OLD_TOKEN:-}"
ADMIN_ID="${OLD_ADMIN:-$ADMIN_ID_DEFAULT}"

if [ -z "$BOT_TOKEN" ]; then
  echo ""
  echo "BOT_TOKEN не найден в .env"
  echo -n "Вставьте токен бота от @BotFather и нажмите Enter: "
  read -r BOT_TOKEN
fi

if [ -z "$BOT_TOKEN" ]; then
  echo "ERROR: BOT_TOKEN пустой. Остановка."
  exit 1
fi

echo "==> Пишем .env"
# Для 1win secret в URL обычно нельзя — оставляем пустым
cat > .env << EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_ID=${ADMIN_ID}
REFERRAL_URL=${REFERRAL_DEFAULT}
PUBLIC_BASE_URL=http://${PUBLIC_IP}:${BOT_PORT}
ALLOW_HTTP_IP=true
POSTBACK_SECRET=
POSTBACK_CLICK_ID_PARAM=click_id
POSTBACK_EVENT_PARAM=event
POSTBACK_SECRET_PARAM=secret
POSTBACK_SUCCESS_EVENT=registration
HTTP_HOST=0.0.0.0
HTTP_PORT=${BOT_PORT}
ACTIVATION_NOTIFICATION_DELAY=120
SIGNAL_LIFETIME=240
ADMIN_NOTIFY_ON_NEW_USER=true
ADMIN_NOTIFY_ON_ACTIVATION=true
ADMIN_NOTIFY_ON_SIGNAL=false
EOF

echo "==> Ставим cloudflared"
ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64) CF_ARCH="amd64" ;;
  aarch64|arm64) CF_ARCH="arm64" ;;
  *) CF_ARCH="amd64" ;;
esac
curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
cloudflared --version || true

echo "==> Останавливаем старые процессы"
pkill -f "python bot.py" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

echo "==> Запускаем бота"
nohup python bot.py > bot_run.log 2>&1 &
sleep 3

if ! grep -q "Starting Telegram polling" bot_run.log 2>/dev/null; then
  echo "WARNING: бот мог не стартовать. Лог:"
  tail -40 bot_run.log || true
fi

echo "==> Запускаем Cloudflare tunnel (HTTPS без домена)"
rm -f cloudflared.log
nohup cloudflared tunnel --url "http://127.0.0.1:${BOT_PORT}" > cloudflared.log 2>&1 &
sleep 5

TUNNEL_URL=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  TUNNEL_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' cloudflared.log | head -1 || true)
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
  sleep 2
done

if [ -z "$TUNNEL_URL" ]; then
  echo "ERROR: не удалось получить HTTPS URL от Cloudflare."
  echo "Смотрите: tail -50 cloudflared.log"
  tail -50 cloudflared.log || true
  exit 1
fi

echo "==> Обновляем PUBLIC_BASE_URL -> $TUNNEL_URL"
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${TUNNEL_URL}|" .env

echo "==> Перезапускаем бота с новым PUBLIC_BASE_URL"
pkill -f "python bot.py" 2>/dev/null || true
sleep 1
nohup python bot.py > bot_run.log 2>&1 &
sleep 3

POSTBACK_LINE="${TUNNEL_URL}/postback?sub1={sub1}"

echo ""
echo "=============================================="
echo " ГОТОВО"
echo "=============================================="
echo " Cloudflare URL:  $TUNNEL_URL"
echo ""
echo " Вставьте в 1win → поле «Регистрация»:"
echo " $POSTBACK_LINE"
echo ""
echo " Проверка бота в Telegram: /start"
echo " Логи бота:        tail -f /root/mine/bot_run.log"
echo " Логи туннеля:     tail -f /root/mine/cloudflared.log"
echo "=============================================="
echo "$POSTBACK_LINE" > /root/mine/POSTBACK_FOR_1WIN.txt
echo "Строка также сохранена в /root/mine/POSTBACK_FOR_1WIN.txt"
