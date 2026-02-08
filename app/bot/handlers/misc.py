from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.i18n import get_lang, t

router = Router()


@router.callback_query(F.data.startswith('report:'))
async def report_callback(callback: CallbackQuery) -> None:
    await callback.message.answer(t(get_lang(callback.from_user), "report_thanks"))
    await callback.answer()
