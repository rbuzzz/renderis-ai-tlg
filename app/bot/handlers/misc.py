from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery


router = Router()


@router.callback_query(F.data.startswith('report:'))
async def report_callback(callback: CallbackQuery) -> None:
    await callback.message.answer('Спасибо за сообщение! Мы проверим результат.')
    await callback.answer()
