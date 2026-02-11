from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.keyboards.main import language_menu, main_menu, settings_menu, terms_blocked_menu, terms_menu
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import Price
from app.i18n import normalize_lang
from app.services.credits import CreditsService
from app.utils.text import escape_html


router = Router()
TERMS_DOC_URL = "https://rebrand.ly/8da8cc"


def _language_label(code: str) -> str:
    return {
        "en": "English",
        "es": "Espanol",
        "ru": "Русский",
    }.get(code, "English")


def _terms_accepted(settings_payload: dict) -> bool:
    return bool(settings_payload.get("terms_accepted"))


def _normalize_settings_payload(raw_settings: object) -> dict:
    if isinstance(raw_settings, dict):
        return dict(raw_settings)
    return {}


def _terms_prompt_text(lang: str) -> str:
    return (
        f"{t(lang, 'terms_intro')}\n"
        f"{tf(lang, 'terms_link', link=TERMS_DOC_URL)}\n\n"
        f"{t(lang, 'terms_question')}"
    )


async def _send_post_accept_intro(
    message: Message,
    session: AsyncSession,
    user,
    lang: str,
    full_name: str,
    settings_payload: dict,
) -> None:
    settings = get_settings()
    credits = CreditsService(session)
    bonus_applied = await credits.apply_signup_bonus(user, settings.signup_bonus_credits)
    await session.commit()

    text = (
        f"{tf(lang, 'start_hello', name=escape_html(full_name))}\n"
        f"{tf(lang, 'start_balance', credits=user.balance_credits)}\n"
        f"{t(lang, 'start_terms')}"
    )
    if bonus_applied:
        text += f"\n{tf(lang, 'start_bonus', credits=settings.signup_bonus_credits)}"
    await message.answer(text, reply_markup=main_menu(lang))

    if not settings_payload.get("lang_selected"):
        await message.answer(
            t(lang, "settings_lang_prompt_first"),
            reply_markup=language_menu(current_lang=lang, lang=lang, include_back=False),
        )


@router.message(Command("start"))
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.id in settings.admin_ids(),
    )
    settings_payload = _normalize_settings_payload(user.settings)
    lang = normalize_lang(settings_payload.get("lang"))
    settings_payload["lang"] = lang
    user.settings = settings_payload

    if not _terms_accepted(settings_payload):
        await session.commit()
        await message.answer(
            _terms_prompt_text(lang),
            reply_markup=terms_menu(lang),
            disable_web_page_preview=True,
        )
        return

    await session.commit()
    await message.answer(t(lang, "settings_back_to_main"), reply_markup=main_menu(lang))


@router.callback_query(F.data == "terms:read")
async def terms_read(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.id in settings.admin_ids(),
    )
    settings_payload = _normalize_settings_payload(user.settings)
    lang = normalize_lang(settings_payload.get("lang"))
    settings_payload["lang"] = lang
    user.settings = settings_payload
    await session.commit()

    await callback.message.answer(
        _terms_prompt_text(lang),
        reply_markup=terms_menu(lang),
        disable_web_page_preview=True,
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "terms:decline")
async def terms_decline(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.id in settings.admin_ids(),
    )
    settings_payload = _normalize_settings_payload(user.settings)
    lang = normalize_lang(settings_payload.get("lang"))
    settings_payload["lang"] = lang
    settings_payload["terms_accepted"] = False
    settings_payload["terms_declined"] = True
    user.settings = settings_payload
    await session.commit()

    await callback.message.answer(
        t(lang, "terms_declined_blocked"),
        reply_markup=terms_blocked_menu(lang),
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "terms:accept")
async def terms_accept(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.id in settings.admin_ids(),
    )
    settings_payload = _normalize_settings_payload(user.settings)
    lang = normalize_lang(settings_payload.get("lang"))
    settings_payload["lang"] = lang
    settings_payload["terms_accepted"] = True
    settings_payload["terms_declined"] = False
    user.settings = settings_payload

    await _send_post_accept_intro(
        callback.message,
        session,
        user,
        lang,
        callback.from_user.full_name,
        settings_payload,
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery) -> None:
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "help_text"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "prices:list")
async def show_prices(callback: CallbackQuery, session: AsyncSession) -> None:
    price_map = await _get_price_map(session)
    lang = get_lang(callback.from_user)
    lines = _format_price_list(price_map, lang)
    await callback.message.answer(lines)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "settings:open")
async def open_settings(callback: CallbackQuery) -> None:
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "settings_title"), reply_markup=settings_menu(lang))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "settings:back")
async def back_from_settings(callback: CallbackQuery) -> None:
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "settings_back_to_main"), reply_markup=main_menu(lang))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "settings:language")
async def open_language_settings(callback: CallbackQuery, session: AsyncSession) -> None:
    lang = get_lang(callback.from_user)
    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    current = normalize_lang((user.settings or {}).get("lang")) if user else lang
    await callback.message.answer(
        t(lang, "settings_lang_prompt"),
        reply_markup=language_menu(current_lang=current, lang=lang, include_back=True),
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith("settings:lang:"))
async def set_user_language(callback: CallbackQuery, session: AsyncSession) -> None:
    selected = normalize_lang(callback.data.split(":", 2)[2])
    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if user:
        payload = _normalize_settings_payload(user.settings)
        payload["lang"] = selected
        payload["lang_selected"] = True
        user.settings = payload
        await session.commit()

    await callback.message.answer(
        tf(selected, "settings_lang_saved", language=_language_label(selected)),
        reply_markup=settings_menu(selected),
    )
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
