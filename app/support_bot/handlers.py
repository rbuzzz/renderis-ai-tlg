from __future__ import annotations

from aiogram import F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.credits import CreditsService
from app.services.support import SupportService


router = Router()


def _message_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.photo:
        return "📷 Фото"
    if message.document:
        name = message.document.file_name or "файл"
        return f"📎 Документ: {name}"
    return f"[{message.content_type}]"


async def _notify_admins(thread_id: int, user_label: str, text: str) -> None:
    settings = get_settings()
    admin_ids = settings.admin_ids()
    if not admin_ids:
        return

    preview = text.strip()
    if len(preview) > 400:
        preview = preview[:400] + "…"

    buttons = []
    if settings.admin_web_public_url:
        buttons.append(
            InlineKeyboardButton(
                text="Перейти в чат",
                url=f"{settings.admin_web_public_url}/admin/chats?thread={thread_id}",
            )
        )
    buttons.append(InlineKeyboardButton(text="Ответить в боте", callback_data=f"support:reply:{thread_id}"))
    buttons.append(InlineKeyboardButton(text="Шаблоны", callback_data=f"support:templates:{thread_id}"))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons[:2], buttons[2:]])
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        for admin_id in admin_ids:
            await bot.send_message(
                admin_id,
                f"📩 Сообщение в поддержку\n<b>{user_label}</b>\n\n{preview}",
                reply_markup=keyboard,
            )
    finally:
        await bot.session.close()


@router.message(F.text == "/start")
async def support_start(message: Message) -> None:
    await message.answer("Привет! Опишите вопрос или проблему — мы ответим.")


@router.message()
async def support_message(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    settings = get_settings()
    if not settings.support_bot_token:
        await message.answer("Служба поддержки временно недоступна.")
        return

    credits = CreditsService(session)
    is_admin = message.from_user.id in settings.admin_ids()
    user = await credits.ensure_user(message.from_user.id, message.from_user.username, is_admin)
    support = SupportService(session)
    thread = await support.ensure_thread(user)

    text = _message_text(message)
    await support.add_message(thread, "user", text, tg_message_id=message.message_id)
    await session.commit()

    label = user.username or str(user.telegram_id)
    await _notify_admins(thread.id, label, text)
    await message.answer("Спасибо! Мы скоро ответим.")
