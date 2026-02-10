from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.i18n import reset_current_lang, set_current_lang
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
            if user and getattr(user, "id", None):
                result = await session.execute(select(User.settings).where(User.telegram_id == int(user.id)))
                settings = result.scalar_one_or_none()
                if isinstance(settings, dict):
                    lang = normalize_lang(settings.get("lang"))

            token = set_current_lang(lang)
            try:
                return await handler(event, data)
            finally:
                reset_current_lang(token)
