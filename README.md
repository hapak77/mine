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

Кнопка «Пройти регистрацию» ведёт **сразу** на вашу рефку 1win:

```
https://r1wtvmb.life/casino/list?open=register&p=fiyw&sub1=<CLICK_ID>
```

Прокладки нет. `CLICK_ID` = UUID в SQLite (не Telegram ID).

## Что вписать в кабинет 1win (простыми словами)

1. Поднимите бота на сервере с HTTPS (например `https://myserver.com`).
2. В `.env` укажите:
   - `PUBLIC_BASE_URL=https://myserver.com`
   - `POSTBACK_SECRET=любой_пароль`
   - `REFERRAL_URL=https://r1wtvmb.life/casino/list?open=register&p=fiyw`
3. В кабинете 1win откройте настройку postback.
4. В поле **«Ссылка на постбэк события — Регистрация»** вставьте:

```
https://myserver.com/postback?click_id={sub1}&event=registration&secret=любой_пароль
```

(тот же домен, что в `PUBLIC_BASE_URL`, тот же пароль, что в `POSTBACK_SECRET`)

5. Поля «Доход» / депозит **не трогайте** — для бота нужна только **Регистрация**.

Если макрос в кабинете называется не `{sub1}`, а иначе — посмотрите подсказку в 1win и напишите нам точное имя.