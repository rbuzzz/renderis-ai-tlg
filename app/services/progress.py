from __future__ import annotations

import asyncio
import time
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import Generation
from app.services.latency import ModelLatencyService
from app.utils.time import utcnow


class ProgressService:
    def __init__(self, bot: Bot, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.settings = get_settings()

    def start(
        self,
        generation_id: int,
        chat_id: int,
        message_id: int,
        model_key: str,
        lang: str,
    ) -> None:
        asyncio.create_task(
            self._run_progress(generation_id, chat_id, message_id, model_key, lang)
        )

    async def _get_avg_seconds(self, model_key: str) -> float:
        async with self.sessionmaker() as session:
            latency = ModelLatencyService(session)
            avg = await latency.get_avg_seconds(model_key)
        if avg and avg > 0:
            return avg
        return float(self.settings.model_latency_default_seconds)

    async def _run_progress(
        self,
        generation_id: int,
        chat_id: int,
        message_id: int,
        model_key: str,
        lang: str,
    ) -> None:
        from app.i18n import tf

        avg_seconds = await self._get_avg_seconds(model_key)
        start = time.monotonic()
        last_pct: Optional[int] = None

        while True:
            status = await self._get_generation_status(generation_id)
            if status in ("success", "fail", "partial"):
                break

            elapsed = time.monotonic() - start
            t = min(elapsed / max(avg_seconds, 1.0), 1.0)
            eased = 1 - (1 - t) ** 2
            pct = int(90 * eased)
            pct = max(1, min(90, pct))
            if last_pct != pct:
                await self._safe_edit(chat_id, message_id, tf(lang, "progress_label", pct=pct))
                last_pct = pct
            await asyncio.sleep(2)

        await self._safe_edit(chat_id, message_id, tf(lang, "progress_label", pct=100))
        await asyncio.sleep(0.6)
        await self._safe_delete(chat_id, message_id)
        await self._clear_progress_message(generation_id)

    async def _get_generation_status(self, generation_id: int) -> str:
        async with self.sessionmaker() as session:
            generation = await session.get(Generation, generation_id)
            if not generation:
                return "unknown"
            return generation.status or "unknown"

    async def _clear_progress_message(self, generation_id: int) -> None:
        async with self.sessionmaker() as session:
            generation = await session.get(Generation, generation_id)
            if not generation:
                return
            generation.progress_message_id = None
            generation.updated_at = utcnow()
            await session.commit()

    async def _safe_edit(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            await self.bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            return
        except Exception:
            return

    async def _safe_delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            return
        except Exception:
            return
