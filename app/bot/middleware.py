from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.i18n import reset_current_lang, set_current_lang, t
from app.bot.keyboards.main import terms_blocked_menu
from app.db.models import User
from app.i18n import normalize_lang


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @staticmethod
    def _extract_telegram_user(event: Any, data: Dict[str, Any]) -> Any | None:
        user = data.get("event_from_user")
        if user:
            return user
        direct = getattr(event, "from_user", None)
        if direct:
            return direct
        message = getattr(event, "message", None)
        if message and getattr(message, "from_user", None):
            return message.from_user
        callback_query = getattr(event, "callback_query", None)
        if callback_query and getattr(callback_query, "from_user", None):
            return callback_query.from_user
        return None

    @staticmethod
    def _terms_accepted(user_settings: dict | None) -> bool:
        if not isinstance(user_settings, dict):
            return False
        return bool(user_settings.get("terms_accepted"))

    @staticmethod
    def _is_allowed_without_terms(event: Any) -> bool:
        if isinstance(event, CallbackQuery):
            data = (event.data or "").strip().lower()
            return data.startswith("terms:")
        if isinstance(event, Message):
            text = (event.text or "").strip().lower()
            return text.startswith("/start")
        return False

    @staticmethod
    async def _notify_terms_blocked(event: Any, lang: str) -> None:
        notice = t(lang, "terms_blocked_notice")
        if isinstance(event, CallbackQuery):
            await event.answer(t(lang, "terms_blocked_alert"), show_alert=True)
            if event.message:
                await event.message.answer(notice, reply_markup=terms_blocked_menu(lang))
            return
        if isinstance(event, Message):
            await event.answer(notice, reply_markup=terms_blocked_menu(lang))

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        async with self._sessionmaker() as session:
            data["session"] = session
            lang = "en"
            user = self._extract_telegram_user(event, data)
            settings: dict | None = None
            is_admin = False
            if user and getattr(user, "id", None):
                result = await session.execute(
                    select(User.settings, User.is_admin).where(User.telegram_id == int(user.id))
                )
                row = result.one_or_none()
                if row:
                    settings = row[0] if isinstance(row[0], dict) else None
                    is_admin = bool(row[1])
                if isinstance(settings, dict):
                    lang = normalize_lang(settings.get("lang"))

            token = set_current_lang(lang)
            try:
                if user and getattr(user, "id", None) and settings is not None and not is_admin:
                    if not self._terms_accepted(settings) and not self._is_allowed_without_terms(event):
                        await self._notify_terms_blocked(event, lang)
                        return None
                return await handler(event, data)
            finally:
                reset_current_lang(token)
