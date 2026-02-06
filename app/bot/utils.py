from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message


async def safe_delete_message(message: Message | None) -> None:
    if not message:
        return
    try:
        await message.delete()
    except TelegramBadRequest:
        return
    except Exception:
        return


async def safe_cleanup_callback(callback: CallbackQuery) -> None:
    await safe_delete_message(callback.message)
