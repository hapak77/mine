# MineSlot Signal Bot — Telegram ↔ 1win

Бот на aiogram 3 + SQLite.  
Активация только после postback `registration` от 1win.

Картинки прогнозов кладите в папку `signals/` (1–8 или сколько угодно) — бот сам берёт случайную.

## Структура

```
project/
├── bot.py
├── .env.example
├── requirements.txt
├── signals/          ← ваши картинки (jpg/png/webp)
├── bot.db
└── bot.log
```

## Установка на VPS

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## .env — что заполнить

| Поле | Пример |
|---|---|
| `BOT_TOKEN` | токен BotFather |
| `ADMIN_ID` | ваш Telegram ID |
| `REFERRAL_URL` | `https://r1wtvmb.life/casino/list?open=register&p=fiyw` |
| `PUBLIC_BASE_URL` | `http://ВАШ_IP:8080` |
| `POSTBACK_SECRET` | любой пароль, например `qwerty123` |
| `HTTP_PORT` | `8080` |

Домен **не обязателен**. Достаточно публичного IP VPS.

Откройте порт в firewall:

```bash
# Ubuntu/Debian пример
sudo ufw allow 8080/tcp
sudo ufw reload
```

## Что вписать в кабинет 1win

Поле **«Ссылка на постбэк события — Регистрация»**:

```
http://ВАШ_IP:8080/postback?click_id={sub1}&event=registration&secret=qwerty123
```

Пример (подставьте свой IP):

```
http://1.2.3.4:8080/postback?click_id={sub1}&event=registration&secret=qwerty123
```

При запуске бот сам печатает готовую строку в лог.

Поля «Доход» / депозит не заполняйте.

> Если 1win вдруг отвергнет HTTP и потребует HTTPS — тогда нужен домен или туннель.  
> Для большинства кабинетов HTTP на IP работает.

## Картинки

Положите файлы в `signals/`:

```
signals/
  signal1.jpg
  signal2.png
  ...
```

Сколько угодно (1, 8, 20). Бот при каждом прогнозе выбирает случайную и не повторяет ту же подряд, если картинок больше одной.

## Тест postback

```bash
curl "http://ВАШ_IP:8080/postback?click_id=ТЕСТОВЫЙ_CLICK_ID&event=registration&secret=qwerty123"
```

## Админ

- `/stats`
- `/users`
