from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CreditLedger, Generation, GenerationTask, Order, ReferralCode, ReferralUse, User
from app.utils.time import utcnow


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def dashboard(self, days: int = 1) -> dict:
        since = utcnow() - timedelta(days=days)

        revenue = await self.session.execute(
            select(func.coalesce(func.sum(Order.stars_amount), 0)).where(Order.created_at >= since)
        )
        revenue_stars = int(revenue.scalar_one() or 0)

        credits_issued = await self.session.execute(
            select(func.coalesce(func.sum(CreditLedger.delta_credits), 0))
            .where(CreditLedger.created_at >= since)
            .where(CreditLedger.delta_credits > 0)
        )
        credits_issued_val = int(credits_issued.scalar_one() or 0)

        credits_spent = await self.session.execute(
            select(func.coalesce(func.sum(CreditLedger.delta_credits), 0))
            .where(CreditLedger.created_at >= since)
            .where(CreditLedger.reason == 'generation_charge')
        )
        credits_spent_val = abs(int(credits_spent.scalar_one() or 0))

        top_models = await self.session.execute(
            select(Generation.model, func.count(Generation.id))
            .where(Generation.created_at >= since)
            .group_by(Generation.model)
            .order_by(func.count(Generation.id).desc())
            .limit(5)
        )
        top_models_list = [(row[0], int(row[1])) for row in top_models.all()]

        active_users = await self.session.execute(
            select(func.count(func.distinct(Generation.user_id)))
            .where(Generation.created_at >= since)
        )
        active_users_val = int(active_users.scalar_one() or 0)

        total_users = await self.session.execute(select(func.count(User.id)))
        total_users_val = int(total_users.scalar_one() or 0)

        paying_users = await self.session.execute(
            select(func.count(func.distinct(Order.user_id))).where(Order.created_at >= since)
        )
        paying_users_val = int(paying_users.scalar_one() or 0)
        conversion = (paying_users_val / total_users_val * 100) if total_users_val else 0.0

        failures = await self.session.execute(
            select(func.count(GenerationTask.id))
            .where(GenerationTask.finished_at.is_not(None))
            .where(GenerationTask.state == 'fail')
            .where(GenerationTask.started_at >= since)
        )
        failures_val = int(failures.scalar_one() or 0)

        total_tasks = await self.session.execute(
            select(func.count(GenerationTask.id))
            .where(GenerationTask.started_at >= since)
        )
        total_tasks_val = int(total_tasks.scalar_one() or 0)
        failure_rate = (failures_val / total_tasks_val * 100) if total_tasks_val else 0.0

        avg_latency = await self.session.execute(
            select(func.avg(func.extract('epoch', GenerationTask.finished_at - GenerationTask.started_at)))
            .where(GenerationTask.finished_at.is_not(None))
            .where(GenerationTask.started_at >= since)
        )
        avg_latency_val = float(avg_latency.scalar_one() or 0.0)

        ref_stats = await self.session.execute(
            select(ReferralCode.code, ReferralCode.discount_pct, ReferralCode.usage_count)
            .order_by(ReferralCode.usage_count.desc())
            .limit(5)
        )
        ref_top = [(r[0], int(r[1]), int(r[2])) for r in ref_stats.all()]

        dau = await self.session.execute(
            select(func.count(func.distinct(Generation.user_id)))
            .where(Generation.created_at >= utcnow() - timedelta(days=1))
        )
        wau = await self.session.execute(
            select(func.count(func.distinct(Generation.user_id)))
            .where(Generation.created_at >= utcnow() - timedelta(days=7))
        )

        return {
            'revenue_stars': revenue_stars,
            'credits_issued': credits_issued_val,
            'credits_spent': credits_spent_val,
            'top_models': top_models_list,
            'conversion_pct': conversion,
            'active_users': active_users_val,
            'dau': int(dau.scalar_one() or 0),
            'wau': int(wau.scalar_one() or 0),
            'failure_rate_pct': failure_rate,
            'avg_latency_sec': avg_latency_val,
            'ref_top': ref_top,
        }
