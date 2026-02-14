from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CreditLedger, User
from app.utils.credits import to_credits
from app.utils.time import utcnow


class CreditsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user(self, telegram_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def ensure_user(self, telegram_id: int, username: Optional[str], is_admin: bool) -> User:
        user = await self.get_user(telegram_id)
        now = utcnow()
        if user:
            user.username = username
            user.last_seen_at = now
            user.is_admin = is_admin
            return user

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_seen_at=now,
            last_seen_at=now,
            is_admin=is_admin,
            is_banned=False,
            balance_credits=to_credits(0),
            settings={},
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def add_ledger(
        self,
        user: User,
        delta: Decimal | int | float | str,
        reason: str,
        meta: dict | None = None,
        idempotency_key: str | None = None,
    ) -> CreditLedger:
        delta_credits = to_credits(delta)
        entry = CreditLedger(
            user_id=user.id,
            delta_credits=delta_credits,
            reason=reason,
            meta=meta or {},
            idempotency_key=idempotency_key,
            created_at=utcnow(),
        )
        user.balance_credits = to_credits(user.balance_credits) + delta_credits
        self.session.add(entry)
        return entry

    async def apply_signup_bonus(self, user: User, bonus: Decimal | int | float | str) -> bool:
        key = f'signup:{user.id}'
        result = await self.session.execute(select(CreditLedger).where(CreditLedger.idempotency_key == key))
        if result.scalar_one_or_none():
            return False
        bonus_credits = to_credits(bonus)
        await self.add_ledger(user, bonus_credits, 'signup_bonus', meta={'bonus': str(bonus_credits)}, idempotency_key=key)
        return True

    async def get_daily_spent(self, user: User) -> Decimal:
        since = utcnow() - timedelta(days=1)
        result = await self.session.execute(
            select(func.coalesce(func.sum(CreditLedger.delta_credits), 0))
            .where(CreditLedger.user_id == user.id)
            .where(CreditLedger.reason == 'generation_charge')
            .where(CreditLedger.created_at >= since)
        )
        total = result.scalar_one() or 0
        return abs(to_credits(total))
