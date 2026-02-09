from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import AdminFlow
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import User
from app.services.credits import CreditsService
from app.services.support import SupportService


router = Router()


async def _is_admin(session: AsyncSession, telegram_id: int) -> bool:
    settings = get_settings()
    if telegram_id in settings.admin_ids():
        return True
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    return bool(user and user.is_admin)


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


def _admin_chat_url(thread_id: int) -> str | None:
    settings = get_settings()
    if not settings.admin_web_public_url:
        return None

    base = settings.admin_web_public_url.rstrip("/")
    if base.endswith("/admin/chats"):
        return f"{base}?thread={thread_id}"
    return f"{base}/admin/chats?thread={thread_id}"


async def _notify_admins(thread_id: int, user_label: str, text: str) -> None:
    settings = get_settings()
    if not settings.support_bot_token:
        return

    admin_ids = settings.admin_ids()
    if not admin_ids:
        return

    preview = text.strip()
    if len(preview) > 400:
        preview = preview[:400] + "..."

    first_row: list[InlineKeyboardButton] = []
    second_row: list[InlineKeyboardButton] = []

    chat_url = _admin_chat_url(thread_id)
    if chat_url:
        first_row.append(InlineKeyboardButton(text="Перейти в чат", url=chat_url))
    first_row.append(InlineKeyboardButton(text="Ответить в боте", callback_data=f"support:reply:{thread_id}"))
    second_row.append(InlineKeyboardButton(text="Шаблоны", callback_data=f"support:templates:{thread_id}"))

    keyboard_rows = [first_row]
    if second_row:
        keyboard_rows.append(second_row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    bot = Bot(
        token=settings.support_bot_token,
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
    await message.answer("Привет! Опишите вопрос или проблему, и мы скоро ответим.")


@router.callback_query(F.data.startswith("support:templates:"))
async def support_templates(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await callback.message.answer("Шаблоны пока не настроены.")
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "support:reply_cancel")
async def support_reply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get("support_prompt_id")
    await state.clear()
    if prompt_id:
        try:
            await callback.message.bot.delete_message(callback.message.chat.id, prompt_id)
        except Exception:
            pass
    await callback.answer("Отменено", show_alert=False)
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith("support:reply:"))
async def support_reply(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    thread_id = int(parts[2])
    await state.set_state(AdminFlow.support_reply)
    prompt = await callback.message.answer(
        "Напишите ответ одним сообщением.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="support:reply_cancel")]]
        ),
    )
    await state.update_data(support_thread_id=thread_id, support_prompt_id=prompt.message_id)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.support_reply)
async def support_reply_send(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.from_user or not await _is_admin(session, message.from_user.id):
        await message.answer("Недостаточно прав.")
        await state.clear()
        return

    data = await state.get_data()
    thread_id = data.get("support_thread_id")
    prompt_id = data.get("support_prompt_id")
    if not thread_id:
        await message.answer("Чат не найден.")
        await state.clear()
        return

    support = SupportService(session)
    thread = await support.get_thread(int(thread_id))
    if not thread:
        await message.answer("Чат не найден.")
        await state.clear()
        return

    result = await session.execute(select(User).where(User.id == thread.user_id))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer("Пользователь не найден.")
        await state.clear()
        return

    text = _message_text(message)
    sent = await message.bot.send_message(user.telegram_id, text)
    await support.add_message(
        thread,
        "admin",
        text,
        sender_admin_id=message.from_user.id,
        tg_message_id=sent.message_id,
    )
    await session.commit()

    await state.clear()
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    await message.answer("Ответ отправлен.")


@router.message()
async def support_message(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    settings = get_settings()
    if not settings.support_bot_token:
        await message.answer("Служба поддержки временно недоступна.")
        return

    if await _is_admin(session, message.from_user.id):
        return

    credits = CreditsService(session)
    user = await credits.ensure_user(message.from_user.id, message.from_user.username, False)
    support = SupportService(session)
    thread = await support.ensure_thread(user)

    text = _message_text(message)
    await support.add_message(thread, "user", text, tg_message_id=message.message_id)
    await session.commit()

    label = user.username or str(user.telegram_id)
    await _notify_admins(thread.id, label, text)
    await message.answer("Спасибо! Мы скоро ответим.")
