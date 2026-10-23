# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.config import settings
from bot.services.billing import TARIFFS, is_dry_run, rubles

BTN_TOPICS = "🚗 По темам"
BTN_MARATHON = "⚡️ Марафон"
BTN_MISTAKES = "🛠 Над ошибками"
BTN_EXAM = "🎯 Экзамен"
BTN_LIVES = "❤️ Жизни"
BTN_REFERRAL = "👥 Друзьям"
BTN_TARIFFS = "💳 Тарифы"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TOPICS), KeyboardButton(text=BTN_MARATHON)],
            [KeyboardButton(text=BTN_MISTAKES), KeyboardButton(text=BTN_EXAM)],
            [KeyboardButton(text=BTN_LIVES), KeyboardButton(text=BTN_REFERRAL)],
            [KeyboardButton(text=BTN_TARIFFS)],
        ],
        resize_keyboard=True,
    )


def choice_keyboard(prefix: str, count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index in range(count):
        row.append(InlineKeyboardButton(text=str(index + 1), callback_data=f"{prefix}:{index}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⏹ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def modules_keyboard(modules: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"mod:{module_id}")]
        for module_id, title in modules
    ]
    rows.append([InlineKeyboardButton(text="⏹ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def restart_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать сначала", callback_data=callback_data)],
            [InlineKeyboardButton(text="⏹ В меню", callback_data="nav:menu")],
        ]
    )


def blocked_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Ссылка для друга", callback_data="ref:show")],
            [InlineKeyboardButton(text="💳 Снять ограничение", callback_data="pay:open")],
            [InlineKeyboardButton(text="⏹ В меню", callback_data="nav:menu")],
        ]
    )


def qual_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пройти 3 вопроса", callback_data="qual:start")],
            [InlineKeyboardButton(text="Сразу в меню", callback_data="nav:menu")],
        ]
    )


def paywall_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    dry = is_dry_run(settings.PAYMENT_PROVIDER_TOKEN)
    for code, tariff in TARIFFS.items():
        if dry:
            rows.append([
                InlineKeyboardButton(
                    text=f"Dry-run: {tariff.title} — без оплаты",
                    callback_data=f"dry:{code}",
                )
            ])
            if settings.ENABLE_TELEGRAM_STARS:
                rows.append([
                    InlineKeyboardButton(
                        text=f"Dry-run Stars: {tariff.title} — {tariff.stars} ⭐",
                        callback_data=f"drystar:{code}",
                    )
                ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=f"ЮKassa: {tariff.title} — {rubles(tariff.amount_rub_kopecks)}",
                    callback_data=f"card:{code}",
                )
            ])
            if settings.ENABLE_TELEGRAM_STARS:
                rows.append([
                    InlineKeyboardButton(
                        text=f"Stars: {tariff.title} — {tariff.stars} ⭐",
                        callback_data=f"star:{code}",
                    )
                ])
    rows.append([InlineKeyboardButton(text="Продолжить бесплатно", callback_data="pay:later")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="📥 Excel-отчёт", callback_data="admin:excel")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin:cast")],
        ]
    )


def audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Всем пользователям", callback_data="cast:all")],
            [InlineKeyboardButton(text="Только бесплатным", callback_data="cast:free")],
            [InlineKeyboardButton(text="Отмена", callback_data="cast:cancel")],
        ]
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отправить", callback_data="cast:go")],
            [InlineKeyboardButton(text="Отмена", callback_data="cast:cancel")],
        ]
    )


def url_keyboard(pairs: list[tuple[str, str]] | None) -> InlineKeyboardMarkup | None:
    if not pairs:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label[:64], url=url)]
            for label, url in pairs
        ]
    )
