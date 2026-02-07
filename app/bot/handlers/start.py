from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import main_menu
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import Price
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
        f"👋 Привет, {escape_html(message.from_user.full_name)}!\n"
        f"💰 Баланс: <b>{user.balance_credits}</b> кредитов.\n"
        "📜 Используя бот, вы подтверждаете соблюдение законов и правил сервиса."
    )
    if bonus_applied:
        text += f"\nБонус за старт: +{settings.signup_bonus_credits} кредитов."
    await message.answer(text, reply_markup=main_menu())


@router.callback_query(F.data == 'help')
async def show_help(callback: CallbackQuery) -> None:
    await callback.message.answer(
        'ℹ️ Это бот для генерации изображений. Используйте меню ниже.\n'
        'Команды: /start /ref CODE /promo CODE /admin (для админов).'
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'prices:list')
async def show_prices(callback: CallbackQuery, session: AsyncSession) -> None:
    price_map = await _get_price_map(session)
    lines = _format_price_list(price_map)
    await callback.message.answer(lines)
    await callback.answer()
    await safe_cleanup_callback(callback)


async def _get_price_map(session: AsyncSession) -> dict[tuple[str, str], int]:
    result = await session.execute(
        select(Price.model_key, Price.option_key, Price.price_credits).where(
            Price.model_key.in_(["nano_banana", "nano_banana_edit", "nano_banana_pro"])
        )
    )
    return {(row[0], row[1]): int(row[2] or 0) for row in result.all()}


def _format_price_list(price_map: dict[tuple[str, str], int]) -> str:
    def get(model_key: str, option_key: str) -> int:
        return int(price_map.get((model_key, option_key), 0))

    nb = get("nano_banana", "base")
    edit = get("nano_banana_edit", "base")

    base = get("nano_banana_pro", "base")
    ref = get("nano_banana_pro", "ref_has")
    res2 = get("nano_banana_pro", "resolution_2k")
    res4 = get("nano_banana_pro", "resolution_4k")

    def bundle(key: str, fallback: int) -> int:
        return get("nano_banana_pro", key) or fallback

    no_ref_1k = bundle("bundle_no_refs_1k", base)
    no_ref_2k = bundle("bundle_no_refs_2k", base + res2)
    no_ref_4k = bundle("bundle_no_refs_4k", base + res4)

    ref_1k = bundle("bundle_refs_1k", base + ref)
    ref_2k = bundle("bundle_refs_2k", base + ref + res2)
    ref_4k = bundle("bundle_refs_4k", base + ref + res4)

    return (
        "🧮 <b>Арифметика расхода кредитов</b>\n"
        "Цены актуальны на момент запроса и меняются мгновенно после правок администратора.\n\n"
        f"🍌 Nano Banana — <b>{nb}</b> кр.\n"
        f"🛠️ Nano Banana Edit — <b>{edit}</b> кр.\n\n"
        f"⭐ Pro без референсов 1K — <b>{no_ref_1k}</b> кр.\n"
        f"⭐ Pro без референсов 2K — <b>{no_ref_2k}</b> кр.\n"
        f"⭐ Pro без референсов 4K — <b>{no_ref_4k}</b> кр.\n\n"
        f"📎 Pro с референсами 1K — <b>{ref_1k}</b> кр.\n"
        f"📎 Pro с референсами 2K — <b>{ref_2k}</b> кр.\n"
        f"📎 Pro с референсами 4K — <b>{ref_4k}</b> кр."
    )
