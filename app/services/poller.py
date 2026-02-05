from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import Generation, GenerationTask, User
from app.services.credits import CreditsService
from app.services.kie_client import KieClient, KieError
from app.utils.logging import get_logger
from app.utils.time import utcnow


logger = get_logger('poller')

SUCCESS_STATUSES = {'success', 'succeeded', 'completed', 'done'}
FAIL_STATUSES = {'fail', 'failed', 'error'}


class PollManager:
    def __init__(self, bot: Bot, sessionmaker: async_sessionmaker[AsyncSession], kie: KieClient) -> None:
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.kie = kie
        self.settings = get_settings()
        self.global_sem = asyncio.Semaphore(self.settings.global_max_poll_concurrency)
        self.user_sems: Dict[int, asyncio.Semaphore] = {}

    def _user_sem(self, user_id: int) -> asyncio.Semaphore:
        if user_id not in self.user_sems:
            self.user_sems[user_id] = asyncio.Semaphore(self.settings.per_user_max_concurrent_jobs)
        return self.user_sems[user_id]

    async def restore_pending(self) -> None:
        async with self.sessionmaker() as session:
            result = await session.execute(
                select(GenerationTask.id)
                .join(Generation, Generation.id == GenerationTask.generation_id)
                .where(GenerationTask.state.in_(['queued', 'running', 'pending']))
            )
            ids = [row[0] for row in result.all()]
        for task_id in ids:
            self.schedule(task_id)

    def schedule(self, task_id: int) -> None:
        asyncio.create_task(self._poll_task(task_id))

    async def _poll_task(self, task_id: int) -> None:
        async with self.global_sem:
            async with self.sessionmaker() as session:
                task = await session.get(GenerationTask, task_id)
                if not task:
                    return
                generation = await session.get(Generation, task.generation_id)
                if not generation:
                    return
                user = await session.get(User, generation.user_id)
                if not user:
                    return
                if task.state in ('success', 'fail'):
                    return
                task.state = 'running'
                await session.commit()

            async with self._user_sem(user.id):
                backoffs = self.settings.poll_backoff_list()
                total_wait = 0
                index = 0

                while total_wait <= self.settings.poll_max_wait_seconds:
                    wait_s = backoffs[min(index, len(backoffs) - 1)]
                    await asyncio.sleep(wait_s)
                    total_wait += wait_s
                    index += 1

                    try:
                        record = await self.kie.get_task(task.task_id)
                    except KieError as exc:
                        if exc.status_code in (429, 500):
                            continue
                        await self._mark_fail(task_id, str(exc), 'KIE_ERROR')
                        return

                    status = self.kie.get_status(record).lower()
                    if status in SUCCESS_STATUSES:
                        urls = self.kie.parse_result_urls(record)
                        await self._mark_success(task_id, urls, record)
                        await self._deliver_results(user.telegram_id, generation, urls)
                        await self._update_generation_status(generation.id)
                        return
                    if status in FAIL_STATUSES:
                        fail_code, fail_msg = self.kie.get_fail_info(record)
                        await self._mark_fail(task_id, fail_msg or 'Ошибка генерации', fail_code)
                        await self._maybe_refund(generation.id)
                        await self._notify_failure(user.telegram_id, fail_msg)
                        await self._update_generation_status(generation.id)
                        return

                await self._mark_pending(task_id)
                asyncio.create_task(self._delayed_reschedule(task_id))

    async def _delayed_reschedule(self, task_id: int) -> None:
        await asyncio.sleep(30)
        self.schedule(task_id)

    async def _mark_success(self, task_id: int, urls: List[str], record: dict) -> None:
        async with self.sessionmaker() as session:
            task = await session.get(GenerationTask, task_id)
            if not task:
                return
            task.state = 'success'
            task.result_urls = urls
            task.finished_at = utcnow()
            task.raw_response = record
            await session.commit()

    async def _mark_fail(self, task_id: int, msg: str, code: Optional[str]) -> None:
        async with self.sessionmaker() as session:
            task = await session.get(GenerationTask, task_id)
            if not task:
                return
            task.state = 'fail'
            task.fail_msg = msg
            task.fail_code = code
            task.finished_at = utcnow()
            await session.commit()

    async def _mark_pending(self, task_id: int) -> None:
        async with self.sessionmaker() as session:
            task = await session.get(GenerationTask, task_id)
            if not task:
                return
            task.state = 'pending'
            await session.commit()

    async def _update_generation_status(self, generation_id: int) -> None:
        async with self.sessionmaker() as session:
            generation = await session.get(Generation, generation_id)
            if not generation:
                return
            result = await session.execute(
                select(GenerationTask.state).where(GenerationTask.generation_id == generation_id)
            )
            states = [row[0] for row in result.all()]
            if not states:
                return
            if all(s == 'success' for s in states):
                generation.status = 'success'
            elif all(s == 'fail' for s in states):
                generation.status = 'fail'
            elif any(s == 'fail' for s in states) and any(s == 'success' for s in states):
                generation.status = 'partial'
            else:
                generation.status = 'running'
            generation.updated_at = utcnow()
            await session.commit()

            if generation.status in ('success', 'fail', 'partial'):
                await self._cleanup_reference_files(generation)

    async def _cleanup_reference_files(self, generation: Generation) -> None:
        options = generation.options or {}
        files = options.get('reference_files') or []
        if not files:
            return
        parents = set()
        for path in files:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.warning('ref_delete_failed', path=path, error=str(exc))
            parents.add(os.path.dirname(path))

        for parent in parents:
            try:
                os.rmdir(parent)
            except OSError:
                pass

    async def _deliver_results(self, telegram_id: int, generation: Generation, urls: List[str]) -> None:
        if not urls:
            await self.bot.send_message(telegram_id, 'Генерация завершена, но ссылки не получены.')
            return
        try:
            if len(urls) == 1:
                await self.bot.send_photo(telegram_id, urls[0])
            else:
                media = [InputMediaPhoto(media=u) for u in urls[:10]]
                await self.bot.send_media_group(telegram_id, media=media)
            await self.bot.send_message(
                telegram_id,
                'Готово. Хотите сообщить о проблеме?',
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text='Сообщить', callback_data=f'report:{generation.id}')]]
                ),
            )
        except Exception as exc:
            logger.warning('deliver_failed', error=str(exc))
            await self.bot.send_message(telegram_id, 'Не удалось отправить результат. Попробуйте позже.')

    async def _notify_failure(self, telegram_id: int, msg: Optional[str]) -> None:
        text = 'Генерация не удалась.'
        if msg:
            text += f' Причина: {msg}'
        await self.bot.send_message(telegram_id, text)

    async def _maybe_refund(self, generation_id: int) -> None:
        if not self.settings.refund_on_fail:
            return
        async with self.sessionmaker() as session:
            generation = await session.get(Generation, generation_id)
            if not generation:
                return
            user = await session.get(User, generation.user_id)
            if not user:
                return
            credits = CreditsService(session)
            await credits.add_ledger(
                user,
                generation.final_cost_credits,
                'generation_refund',
                meta={'generation_id': generation_id},
                idempotency_key=f'refund:{generation.generation_order_id}',
            )
            await session.commit()
