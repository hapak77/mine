# MineSlot Signal Bot — Telegram ↔ 1win

MVP на aiogram 3.x + SQLite + aiohttp postback.

Активация = **только** postback события `registration` от 1win.  
Клик по рефке сам по себе пользователя не активирует.

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

В `.env` заполните:

- `BOT_TOKEN` — токен от @BotFather
- `ADMIN_ID` — ваш Telegram ID
- `REFERRAL_URL` — ваша рефка (уже стоит дефолт)
- `PUBLIC_BASE_URL` — HTTPS-адрес сервера с ботом (не localhost)
- `POSTBACK_SECRET` — любой пароль, который сами придумаете

## Запуск

```bash
python bot.py
```

## Кнопка регистрации

Ведёт **сразу** на вашу рефку (без прокладки):

```
https://r1wtvmb.life/casino/list?open=register&p=fiyw&sub1=<CLICK_ID>
```

## Что вписать в кабинет 1win

В поле **«Ссылка на постбэк события — Регистрация»**:

```
https://ВАШ-ДОМЕН/postback?click_id={sub1}&event=registration&secret=ВАШ_ПАРОЛЬ
```

Пример, если сервер `https://myserver.com`, а пароль `qwerty123`:

```
https://myserver.com/postback?click_id={sub1}&event=registration&secret=qwerty123
```

Поля «Доход» / депозит **не заполняйте** для бота — нужна только **Регистрация**.

### Тест

```bash
curl "https://myserver.com/postback?click_id=ТЕСТОВЫЙ_CLICK_ID&event=registration&secret=qwerty123"
```

## Админ-команды

- `/stats` — статистика
- `/users` — последние пользователи
