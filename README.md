# MineSlot Signal Bot

Telegram-бот с прогнозами для MineSlot (aiogram 3.x + SQLite + postback).

## Структура

```
project/
├── bot.py
├── .env.example
├── requirements.txt
├── signals/          # положите сюда JPG/JPEG/PNG/WEBP
├── bot.db            # создаётся автоматически
└── bot.log           # создаётся автоматически
```

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env: BOT_TOKEN, ADMIN_ID, LANDING_URL, POSTBACK_SECRET
```

## Запуск

```bash
python bot.py
```

Бот одновременно:
1. принимает Telegram updates (polling);
2. слушает HTTP postback на `http://0.0.0.0:8080/postback`.

## Postback

По умолчанию ожидается:

```
GET /postback?click_id=UUID&status=reg&secret=YOUR_SECRET
```

Алиасы: `subid` / `sub_id` вместо `click_id`; `event` / `action` вместо `status`; `token` / `key` вместо `secret`.

Адаптация формата партнёрки — только функция `parse_postback_params()` в `bot.py`.

## Команды админа

- `/stats` — статистика
- `/users` — последние пользователи
