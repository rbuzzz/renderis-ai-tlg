from __future__ import annotations

import asyncio
import os
import time
from datetime import timedelta
from typing import Dict, List, Optional

from aiogram import Bot
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.main import generation_result_menu
from app.config import get_settings
from app.db.models import Generation, GenerationTask, User
from app.services.credits import CreditsService
from app.services.kie_client import KieClient, KieError
from app.i18n import normalize_lang, t, tf
from app.utils.logging import get_logger
from app.utils.text import clamp_text, escape_html
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
        self._inflight: set[int] = set()
        self._last_ref_cleanup = 0.0

    def _user_sem(self, user_id: int) -> asyncio.Semaphore:
        if user_id not in self.user_sems:
            self.user_sems[user_id] = asyncio.Semaphore(self.settings.per_user_max_concurrent_jobs)
        return self.user_sems[user_id]

    def _stale_cutoff(self):
        return utcnow() - timedelta(seconds=self.settings.poll_stale_running_seconds)

    async def restore_pending(self) -> None:
        async with self.sessionmaker() as session:
            stale_cutoff = self._stale_cutoff()
            result = await session.execute(
                select(GenerationTask.id)
                .join(Generation, Generation.id == GenerationTask.generation_id)
                .where(
                    or_(
                        GenerationTask.state.in_(['queued', 'pending']),
                        and_(
                            GenerationTask.state == 'running',
                            or_(
                                GenerationTask.started_at.is_(None),
                                GenerationTask.started_at <= stale_cutoff,
                            ),
                        ),
                    )
                )
            )
            ids = [row[0] for row in result.all()]
        for task_id in ids:
            self.schedule(task_id)

    def schedule(self, task_id: int) -> None:
        if task_id in self._inflight:
            return
        self._inflight.add(task_id)
        asyncio.create_task(self._poll_task(task_id))

    async def watch_pending(self, interval: int = 30) -> None:
        while True:
            try:
                async with self.sessionmaker() as session:
                    stale_cutoff = self._stale_cutoff()
                    result = await session.execute(
                        select(GenerationTask.id)
                        .where(
                            or_(
                                GenerationTask.state.in_(['queued', 'pending']),
                                and_(
                                    GenerationTask.state == 'running',
                                    or_(
                                        GenerationTask.started_at.is_(None),
                                        GenerationTask.started_at <= stale_cutoff,
                                    ),
                                ),
                            )
                        )
                    )
                    ids = [row[0] for row in result.all()]
                for task_id in ids:
                    self.schedule(task_id)
                await self._maybe_cleanup_reference_files()
            except Exception as exc:
                logger.warning('poll_watch_failed', error=str(exc))
            await asyncio.sleep(interval)

    async def _claim_task(self, session: AsyncSession, task_id: int) -> bool:
        stale_cutoff = self._stale_cutoff()
        stmt = (
            update(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                or_(
                    GenerationTask.state.in_(['queued', 'pending']),
                    and_(
                        GenerationTask.state == 'running',
                        or_(
                            GenerationTask.started_at.is_(None),
                            GenerationTask.started_at <= stale_cutoff,
                        ),
                    ),
                ),
            )
            .values(state='running', started_at=utcnow())
            .returning(GenerationTask.id)
        )
        result = await session.execute(stmt)
        claimed = result.scalar_one_or_none()
        if not claimed:
            await session.rollback()
            return False
        await session.commit()
        return True

    async def _poll_task(self, task_id: int) -> None:
        try:
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
                    claimed = await self._claim_task(session, task_id)
                    if not claimed:
                        return

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
                            await self._deliver_results(user, generation, urls)
                            await self._update_generation_status(generation.id)
                            return
                        if status in FAIL_STATUSES:
                            fail_code, fail_msg = self.kie.get_fail_info(record)
                            await self._mark_fail(task_id, fail_msg or 'Ошибка генерации', fail_code)
                            await self._maybe_refund(generation.id)
                            await self._notify_failure(user, fail_msg)
                            await self._update_generation_status(generation.id)
                            return

                    await self._mark_pending(task_id)
                    asyncio.create_task(self._delayed_reschedule(task_id))
        finally:
            self._inflight.discard(task_id)

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

    async def _maybe_cleanup_reference_files(self) -> None:
        ttl_hours = self.settings.reference_files_ttl_hours
        if ttl_hours <= 0:
            return
        now = time.time()
        if now - self._last_ref_cleanup < 3600:
            return
        self._last_ref_cleanup = now
        base = self.settings.reference_storage_path
        cutoff = now - ttl_hours * 3600
        if not os.path.isdir(base):
            return
        for entry in os.scandir(base):
            if not entry.is_dir():
                continue
            try:
                newest = 0.0
                for child in os.scandir(entry.path):
                    if child.is_file():
                        newest = max(newest, child.stat().st_mtime)
                if newest and newest > cutoff:
                    continue
                for child in os.scandir(entry.path):
                    if child.is_file():
                        os.remove(child.path)
                try:
                    os.rmdir(entry.path)
                except OSError:
                    pass
            except Exception as exc:
                logger.warning('ref_cleanup_failed', path=entry.path, error=str(exc))

    async def _deliver_results(self, user: User, generation: Generation, urls: List[str]) -> None:
        lang = normalize_lang((user.settings or {}).get("lang"))
        if not urls:
            await self.bot.send_message(user.telegram_id, t(lang, "result_no_urls"))
            return
        try:
            prompt_short = clamp_text(generation.prompt or '', 800)
            caption = tf(lang, "result_caption", prompt=escape_html(prompt_short))
            for url in urls:
                try:
                    await self.bot.send_document(user.telegram_id, url, caption=t(lang, "result_original"))
                except Exception as exc:
                    logger.warning('send_document_failed', url=url, error=str(exc))
                if self._is_image_url(url):
                    try:
                        await self.bot.send_photo(user.telegram_id, url, caption=caption)
                    except Exception as exc:
                        logger.warning('send_photo_failed', url=url, error=str(exc))
            await self.bot.send_message(
                user.telegram_id,
                t(lang, "result_next"),
                reply_markup=generation_result_menu(generation.id, lang=lang),
            )
        except Exception as exc:
            logger.warning('deliver_failed', error=str(exc))
            await self.bot.send_message(user.telegram_id, t(lang, "result_send_failed"))

    @staticmethod
    def _is_image_url(url: str) -> bool:
        lowered = url.lower()
        return lowered.endswith(('.png', '.jpg', '.jpeg', '.webp'))

    async def _notify_failure(self, user: User, msg: Optional[str]) -> None:
        lang = normalize_lang((user.settings or {}).get("lang"))
        if msg:
            text = tf(lang, "generation_failed_reason", reason=msg)
        else:
            text = t(lang, "generation_failed")
        await self.bot.send_message(user.telegram_id, text)

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
