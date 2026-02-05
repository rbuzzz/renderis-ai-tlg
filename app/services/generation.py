from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Generation, GenerationTask, User
from app.modelspecs.base import ModelSpec
from app.services.credits import CreditsService
from app.services.kie_client import KieClient, KieError
from app.services.pricing import PricingService
from app.utils.logging import get_logger
from app.utils.time import utcnow


logger = get_logger('generation')


class GenerationService:
    def __init__(self, session: AsyncSession, kie: KieClient) -> None:
        self.session = session
        self.kie = kie
        self.settings = get_settings()

    async def _count_active_jobs(self, user: User) -> int:
        result = await self.session.execute(
            select(func.count(Generation.id))
            .where(Generation.user_id == user.id)
            .where(Generation.status.in_(['queued', 'running', 'pending']))
        )
        return int(result.scalar_one() or 0)

    def _admin_free_mode(self, user: User) -> bool:
        if not user.is_admin:
            return False
        return bool(user.settings.get('admin_free_mode', self.settings.admin_free_mode_default))

    async def create_generation(
        self,
        user: User,
        model: ModelSpec,
        prompt: str,
        options: Dict[str, Any],
        outputs: int,
    ) -> Generation:
        if user.is_banned:
            raise ValueError('banned')
        if outputs < 1 or outputs > self.settings.max_outputs_per_request:
            raise ValueError('outputs')

        active_jobs = await self._count_active_jobs(user)
        if active_jobs >= self.settings.per_user_max_concurrent_jobs:
            raise ValueError('too_many')

        pricing = PricingService(self.session)
        discount = user.referral_discount_pct or 0
        breakdown = await pricing.resolve_cost(model, options, outputs, discount)

        credits_service = CreditsService(self.session)
        daily_spent = await credits_service.get_daily_spent(user)
        if not user.is_admin and daily_spent + breakdown.total > self.settings.daily_spend_cap_credits:
            raise ValueError('daily_cap')

        if not self._admin_free_mode(user) and user.balance_credits < breakdown.total:
            raise ValueError('no_credits')

        generation = Generation(
            generation_order_id=str(uuid.uuid4()),
            user_id=user.id,
            provider=model.provider,
            model=model.key,
            prompt=prompt,
            options=options,
            outputs_requested=outputs,
            total_cost_credits=breakdown.per_output * outputs,
            discount_pct=discount,
            final_cost_credits=breakdown.total,
            status='queued',
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.session.add(generation)
        await self.session.flush()

        charged = False
        if not self._admin_free_mode(user):
            await credits_service.add_ledger(
                user,
                -breakdown.total,
                'generation_charge',
                meta={'generation_id': generation.id, 'model': model.key},
                idempotency_key=f'gen:{generation.generation_order_id}',
            )
            charged = True

        await self.session.flush()
        try:
            await self._create_tasks(generation, model, prompt, options, outputs)
        except Exception:
            generation.status = 'fail'
            generation.updated_at = utcnow()
            if charged and self.settings.refund_on_fail:
                await credits_service.add_ledger(
                    user,
                    generation.final_cost_credits,
                    'generation_refund',
                    meta={'generation_id': generation.id},
                    idempotency_key=f'refund:{generation.generation_order_id}',
                )
            raise

        generation.status = 'running'
        generation.updated_at = utcnow()
        return generation

    async def _create_tasks(
        self,
        generation: Generation,
        model: ModelSpec,
        prompt: str,
        options: Dict[str, Any],
        outputs: int,
    ) -> None:
        for _ in range(outputs):
            payload = model.build_input(prompt, options, image_inputs=None)
            try:
                data = await self.kie.create_task(model.model_id, payload)
            except KieError as exc:
                if exc.status_code == 429:
                    generation.status = 'pending'
                    generation.updated_at = utcnow()
                    await self.session.flush()
                    raise
                raise
            task_id = str((data.get('data') or {}).get('taskId') or data.get('taskId') or '')
            if not task_id:
                raise ValueError('invalid_task_id')
            task = GenerationTask(
                generation_id=generation.id,
                task_id=task_id,
                state='queued',
                result_urls=[],
                started_at=utcnow(),
            )
            self.session.add(task)
