from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.keyboards.main import main_menu, terms_menu
from app.config import get_settings
from app.i18n import normalize_lang
from app.services.credits import CreditsService

router = Router()
TERMS_DOC_URL = "https://rebrand.ly/8da8cc"


@router.callback_query(F.data.startswith("report:"))
async def report_callback(callback: CallbackQuery) -> None:
    await callback.message.answer(t(get_lang(callback.from_user), "report_thanks"))
    await callback.answer()


@router.message(StateFilter(None), F.chat.type == "private")
async def fallback_private_message(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    credits = CreditsService(session)
    user = await credits.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.id in settings.admin_ids(),
    )

    payload = dict(user.settings) if isinstance(user.settings, dict) else {}
    lang = normalize_lang(payload.get("lang"))
    payload["lang"] = lang
    user.settings = payload
    await session.commit()

    if not bool(payload.get("terms_accepted")):
        terms_text = (
            f"{t(lang, 'terms_intro')}\n"
            f"{tf(lang, 'terms_link', link=TERMS_DOC_URL)}\n\n"
            f"{t(lang, 'terms_question')}"
        )
        await message.answer(
            terms_text,
            reply_markup=terms_menu(lang),
            disable_web_page_preview=True,
        )
        return

    await message.answer(t(lang, "settings_back_to_main"), reply_markup=main_menu(lang))
