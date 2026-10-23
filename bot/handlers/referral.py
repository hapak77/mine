# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.database.models import User
from bot.keyboards.common import BTN_LIVES, BTN_REFERRAL
from bot.services.billing import tariff_label
from bot.services.lives import format_lives, format_msk, is_unlimited, next_life_at
from bot.ui import respond

router = Router(name="referral")
router.message.filter(F.chat.type == "private")


@router.message(F.text == BTN_LIVES)
@router.message(Command("lives"))
async def lives_screen(message: Message, db_user: User) -> None:
    await message.answer(await _text(message, db_user))


@router.message(F.text == BTN_REFERRAL)
@router.message(Command("referral"))
async def referral_screen(message: Message, db_user: User) -> None:
    await message.answer(await _text(message, db_user))


@router.callback_query(F.data == "ref:show")
async def referral_callback(callback: CallbackQuery, db_user: User) -> None:
    await respond(callback, await _text(callback, db_user))
    await callback.answer()


async def _text(event: Message | CallbackQuery, user: User) -> str:
    bot = event.bot
    me = await bot.get_me()
    username = me.username or ""
    link = f"https://t.me/{username}?start={user.referral_code}" if username else user.referral_code
    now = datetime.now(timezone.utc)
    unlimited = is_unlimited(user.premium_forever, user.premium_until, now)
    lines = [
        f"Жизни: {format_lives(user.lives, unlimited=unlimited)}",
        f"Тариф: {tariff_label(user.premium_forever, user.premium_until, now)}",
    ]
    if not unlimited:
        nxt = next_life_at(user.lives, user.regen_anchor)
        if nxt is not None:
            lines.append(f"Следующая жизнь: {format_msk(nxt)}.")
        lines.append(
            "Бесплатно даётся 5 жизней. Ошибка в тренажёре снимает одну, "
            "на нуле тренажёр блокируется, +1 жизнь приходит каждые 3 часа."
        )
    lines.append("")
    lines.append(
        "Отправьте ссылку другу. Когда он впервые запустит бота, "
        "ваши жизни восстановятся до 5 и вам начислят ещё +5."
    )
    lines.append(link)
    return "\n".join(lines)
