#!/usr/bin/env bash
# Очистка базы пользователей + отключение skip регистрации для всех
# (админ по-прежнему заходит без регистрации)
set -euo pipefail

cd /root/mine

systemctl stop mineslot-bot 2>/dev/null || pkill -f "python bot.py" 2>/dev/null || true
sleep 1

rm -f bot.db bot.db-journal bot.db-wal bot.db-shm
echo "База пользователей удалена."

if [ -f .env ]; then
  # убираем DEV_SKIP_ACTIVATION если был
  grep -v '^DEV_SKIP_ACTIVATION=' .env > .env.tmp || true
  mv .env.tmp .env
  echo "DEV_SKIP_ACTIVATION убран из .env"
fi

git pull origin cursor/mineslot-telegram-bot-d3fb || true

if systemctl list-unit-files | grep -q mineslot-bot; then
  systemctl start mineslot-bot
else
  source venv/bin/activate
  nohup python bot.py > bot_run.log 2>&1 &
fi

sleep 2
echo "Готово. Обычные пользователи снова идут через регистрацию."
echo "Админ (ADMIN_ID) — без регистрации."
