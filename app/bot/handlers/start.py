from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import main_menu
from app.config import get_settings
from app.services.credits import CreditsService
from app.utils.text import escape_html


router = Router()


@router.message(Command('start'))
async def cmd_start(message: Message, session: AsyncSession) -> None:
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(message.from_user.id, message.from_user.username, message.from_user.id in settings.admin_ids())
    bonus_applied = await credits.apply_signup_bonus(user, settings.signup_bonus_credits)
    await session.commit()

    text = (
        f"Привет, {escape_html(message.from_user.full_name)}!\n"
        f"Баланс: <b>{user.balance_credits}</b> кредитов.\n"
        "Используя бот, вы подтверждаете соблюдение законов и правил сервиса."
    )
    if bonus_applied:
        text += f"\nБонус за старт: +{settings.signup_bonus_credits} кредитов."
    await message.answer(text, reply_markup=main_menu())


@router.callback_query(F.data == 'help')
async def show_help(callback: CallbackQuery) -> None:
    await callback.message.answer(
        'Это бот для генерации изображений. Используйте меню ниже.\n'
        'Команды: /start /ref CODE /promo CODE /admin (для админов).'
    )
    await callback.answer()
