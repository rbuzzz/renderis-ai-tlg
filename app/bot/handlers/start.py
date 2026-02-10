from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.payments import send_buy_options
from app.bot.i18n import get_lang, t, tf
from app.bot.keyboards.main import main_menu
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import Price
from app.services.credits import CreditsService
from app.utils.text import escape_html


router = Router()


@router.message(Command('start'))
async def cmd_start(message: Message, session: AsyncSession, command: CommandObject) -> None:
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(message.from_user.id, message.from_user.username, message.from_user.id in settings.admin_ids())
    lang = get_lang(message.from_user)
    user.settings["lang"] = lang
    bonus_applied = await credits.apply_signup_bonus(user, settings.signup_bonus_credits)
    await session.commit()

    text = (
        f"{tf(lang, 'start_hello', name=escape_html(message.from_user.full_name))}\n"
        f"{tf(lang, 'start_balance', credits=user.balance_credits)}\n"
        f"{t(lang, 'start_terms')}"
    )
    if bonus_applied:
        text += f"\n{tf(lang, 'start_bonus', credits=settings.signup_bonus_credits)}"
    await message.answer(text, reply_markup=main_menu(lang))

    start_arg = (command.args or "").strip().lower()
    if start_arg in {"buy", "topup", "stars"}:
        await send_buy_options(message, session)


@router.callback_query(F.data == 'help')
async def show_help(callback: CallbackQuery) -> None:
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "help_text"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'prices:list')
async def show_prices(callback: CallbackQuery, session: AsyncSession) -> None:
    price_map = await _get_price_map(session)
    lang = get_lang(callback.from_user)
    lines = _format_price_list(price_map, lang)
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


def _format_price_list(price_map: dict[tuple[str, str], int], lang: str) -> str:
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
        f"{t(lang, 'prices_title')}\n"
        f"{t(lang, 'prices_note')}\n\n"
        f"{tf(lang, 'prices_nb', cost=nb)}\n"
        f"{tf(lang, 'prices_edit', cost=edit)}\n\n"
        f"{tf(lang, 'prices_pro_no_refs_1k', cost=no_ref_1k)}\n"
        f"{tf(lang, 'prices_pro_no_refs_2k', cost=no_ref_2k)}\n"
        f"{tf(lang, 'prices_pro_no_refs_4k', cost=no_ref_4k)}\n\n"
        f"{tf(lang, 'prices_pro_refs_1k', cost=ref_1k)}\n"
        f"{tf(lang, 'prices_pro_refs_2k', cost=ref_2k)}\n"
        f"{tf(lang, 'prices_pro_refs_4k', cost=ref_4k)}"
    )
