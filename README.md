# MineSlot Signal Bot — Telegram ↔ 1win

MVP на aiogram 3.x + SQLite + aiohttp postback.

Активация пользователя = **только** server-to-server postback события `registration`.
Клик по партнёрской ссылке сам по себе пользователя не активирует.

## Структура

```
project/
├── bot.py
├── .env.example
├── requirements.txt
├── signals/          # JPG / JPEG / PNG / WEBP
├── bot.db            # создаётся автоматически
└── bot.log
```

## Установка

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните в `.env`:

- `BOT_TOKEN`
- `ADMIN_ID`
- `LANDING_URL` (уже есть дефолт 1win)
- `PUBLIC_BASE_URL` — публичный HTTPS (не localhost)
- `POSTBACK_SECRET` (если 1win позволяет static-параметр)

## Запуск

```bash
python bot.py
```

Бот одновременно:

1. принимает Telegram updates (polling);
2. слушает `GET/POST /postback` на `HTTP_PORT`.

## Ссылка регистрации

Для каждого пользователя:

```
https://r1wtvmb.life/casino/list?open=register&p=fiyw&sub1=<CLICK_ID>
```

`CLICK_ID` = UUID, хранится в SQLite, **не** Telegram ID.

## Postback для кабинета 1win

В поле **«Ссылка на постбэк события — Регистрация»**:

```
https://YOUR-DOMAIN.COM/postback?click_id={sub1}&event=registration&secret=YOUR_SECRET
```

Подставьте свой `PUBLIC_BASE_URL` и `POSTBACK_SECRET`.
Точное имя макроса `{sub1}` уточните в кабинете 1win.

**Не** используйте postback «Доход» / депозит для активации бота.

### Тестовый запрос

```bash
curl "https://YOUR-DOMAIN.COM/postback?click_id=USER_CLICK_ID&event=registration&secret=YOUR_SECRET"
```

### Смена формата параметров

В `.env` или константах в начале `bot.py`:

- `POSTBACK_CLICK_ID_PARAM`
- `POSTBACK_EVENT_PARAM`
- `POSTBACK_SECRET_PARAM`
- `POSTBACK_SUCCESS_EVENT`

Логика обработки — функция `process_postback()` в `bot.py`.

## Админ-команды

- `/stats` — статистика
- `/users` — последние пользователи
