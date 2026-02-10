from __future__ import annotations

import mimetypes
import uuid
from html import escape
from pathlib import Path

from aiogram import F, Router
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

SUPPORT_MEDIA_DIR = "_support_media"
SUPPORT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


async def _is_admin(session: AsyncSession, telegram_id: int) -> bool:
    settings = get_settings()
    if telegram_id in settings.admin_ids():
        return True
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    return bool(user and user.is_admin)


def _is_image_document(message: Message) -> bool:
    if not message.document:
        return False
    mime = (message.document.mime_type or "").lower()
    if mime.startswith("image/"):
        return True
    ext = Path(message.document.file_name or "").suffix.lower()
    return ext in SUPPORT_IMAGE_EXTENSIONS


def _message_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.photo:
        return "📷 Фото"
    if _is_image_document(message):
        name = message.document.file_name or "image"
        return f"📎 Изображение: {name}"
    if message.document:
        name = message.document.file_name or "файл"
        return f"📎 Документ: {name}"
    return f"[{message.content_type}]"


def _support_media_root(storage_root: str) -> Path:
    return Path(storage_root) / SUPPORT_MEDIA_DIR


def _guess_ext(file_path: str | None, file_name: str | None, mime_type: str | None, default: str = ".jpg") -> str:
    ext = Path(file_name or "").suffix.lower()
    if ext in SUPPORT_IMAGE_EXTENSIONS:
        return ext
    ext = Path(file_path or "").suffix.lower()
    if ext in SUPPORT_IMAGE_EXTENSIONS:
        return ext
    guessed = mimetypes.guess_extension((mime_type or "").lower())
    if guessed and guessed.lower() in SUPPORT_IMAGE_EXTENSIONS:
        return guessed.lower()
    return default


async def _store_telegram_media(
    message: Message,
    thread_id: int,
    file_id: str,
    source_name: str | None = None,
    mime_type: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    settings = get_settings()
    try:
        file = await message.bot.get_file(file_id)
        if not file.file_path:
            return None, source_name, mime_type
        ext = _guess_ext(file.file_path, source_name, mime_type)
        thread_dir = _support_media_root(settings.reference_storage_path) / str(thread_id)
        thread_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        abs_path = thread_dir / filename
        await message.bot.download_file(file.file_path, destination=str(abs_path))
        rel_path = str(Path(SUPPORT_MEDIA_DIR) / str(thread_id) / filename).replace("\\", "/")
        final_name = source_name or filename
        final_mime = mime_type or mimetypes.guess_type(final_name)[0] or "image/jpeg"
        return rel_path, final_name, final_mime
    except Exception:
        return None, source_name, mime_type


def _admin_chat_url(thread_id: int) -> str | None:
    settings = get_settings()
    if not settings.admin_web_public_url:
        return None

    base = settings.admin_web_public_url.rstrip("/")
    if base.endswith("/admin/chats"):
        return f"{base}?thread={thread_id}"
    return f"{base}/admin/chats?thread={thread_id}"


async def _notify_admins(
    message: Message,
    thread_id: int,
    user_label: str,
    text: str,
    media_kind: str | None = None,
    media_file_id: str | None = None,
) -> None:
    settings = get_settings()
    admin_ids = settings.admin_ids()
    if not admin_ids:
        return

    preview = (text or "").strip()
    if len(preview) > 800:
        preview = preview[:800] + "..."
    body = f"📩 Сообщение в поддержку\n<b>{escape(user_label)}</b>\n\n{escape(preview)}"

    first_row: list[InlineKeyboardButton] = []
    second_row: list[InlineKeyboardButton] = []
    chat_url = _admin_chat_url(thread_id)
    if chat_url:
        first_row.append(InlineKeyboardButton(text="Перейти в чат", url=chat_url))
    first_row.append(InlineKeyboardButton(text="Ответить в боте", callback_data=f"support:reply:{thread_id}"))
    second_row.append(InlineKeyboardButton(text="Шаблоны", callback_data=f"support:templates:{thread_id}"))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[first_row, second_row])

    for admin_id in admin_ids:
        if media_kind == "photo" and media_file_id:
            await message.bot.send_photo(admin_id, media_file_id, caption=body, reply_markup=keyboard)
        elif media_kind == "document" and media_file_id:
            await message.bot.send_document(admin_id, media_file_id, caption=body, reply_markup=keyboard)
        else:
            await message.bot.send_message(admin_id, body, reply_markup=keyboard)


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
        "Напишите ответ текстом или отправьте изображение (скриншот) одним сообщением.",
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

    if not message.text and not message.photo and not _is_image_document(message):
        await message.answer("Поддерживаются только текст и изображения (фото или image-документ).")
        return

    text = _message_text(message).strip()
    media_type = None
    media_path = None
    media_file_name = None
    media_mime_type = None

    if message.photo:
        photo = message.photo[-1]
        caption = (message.caption or "").strip()
        text = caption or "📷 Фото"
        sent = await message.bot.send_photo(user.telegram_id, photo.file_id, caption=caption or None)
        media_type = "image"
        media_path, media_file_name, media_mime_type = await _store_telegram_media(
            message,
            int(thread_id),
            photo.file_id,
            source_name="photo.jpg",
            mime_type="image/jpeg",
        )
    elif _is_image_document(message):
        document = message.document
        caption = (message.caption or "").strip()
        text = caption or f"📎 Изображение: {document.file_name or 'image'}"
        sent = await message.bot.send_document(user.telegram_id, document.file_id, caption=caption or None)
        media_type = "image"
        media_path, media_file_name, media_mime_type = await _store_telegram_media(
            message,
            int(thread_id),
            document.file_id,
            source_name=document.file_name,
            mime_type=document.mime_type,
        )
    else:
        sent = await message.bot.send_message(user.telegram_id, text)

    await support.add_message(
        thread,
        "admin",
        text,
        sender_admin_id=message.from_user.id,
        tg_message_id=sent.message_id,
        media_type=media_type,
        media_path=media_path,
        media_file_name=media_file_name,
        media_mime_type=media_mime_type,
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

    if not message.text and not message.photo and not _is_image_document(message):
        await message.answer("Поддерживаются только текст и изображения (скриншоты).")
        return

    credits = CreditsService(session)
    user = await credits.ensure_user(message.from_user.id, message.from_user.username, False)
    support = SupportService(session)
    thread = await support.ensure_thread(user)

    text = _message_text(message).strip()
    media_type = None
    media_path = None
    media_file_name = None
    media_mime_type = None
    notify_media_kind = None
    notify_media_file_id = None

    if message.photo:
        photo = message.photo[-1]
        caption = (message.caption or "").strip()
        text = caption or "📷 Фото"
        media_type = "image"
        media_path, media_file_name, media_mime_type = await _store_telegram_media(
            message,
            thread.id,
            photo.file_id,
            source_name="photo.jpg",
            mime_type="image/jpeg",
        )
        notify_media_kind = "photo"
        notify_media_file_id = photo.file_id
    elif _is_image_document(message):
        document = message.document
        caption = (message.caption or "").strip()
        text = caption or f"📎 Изображение: {document.file_name or 'image'}"
        media_type = "image"
        media_path, media_file_name, media_mime_type = await _store_telegram_media(
            message,
            thread.id,
            document.file_id,
            source_name=document.file_name,
            mime_type=document.mime_type,
        )
        notify_media_kind = "document"
        notify_media_file_id = document.file_id

    await support.add_message(
        thread,
        "user",
        text,
        tg_message_id=message.message_id,
        media_type=media_type,
        media_path=media_path,
        media_file_name=media_file_name,
        media_mime_type=media_mime_type,
    )
    await session.commit()

    label = user.username or str(user.telegram_id)
    await _notify_admins(
        message,
        thread.id,
        label,
        text,
        media_kind=notify_media_kind,
        media_file_id=notify_media_file_id,
    )
    await message.answer("Спасибо! Мы скоро ответим.")
