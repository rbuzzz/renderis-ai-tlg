from __future__ import annotations

import random
import string

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReferralCode, ReferralUse, User
from app.utils.time import utcnow


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _generate_code(self, length: int = 8) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return ''.join(random.choice(alphabet) for _ in range(length))

    async def create_code(self, discount_pct: int, admin_id: int) -> ReferralCode:
        code = self._generate_code()
        ref = ReferralCode(
            code=code,
            discount_pct=discount_pct,
            created_by_admin_id=admin_id,
            created_at=utcnow(),
            active=True,
            usage_count=0,
        )
        self.session.add(ref)
        return ref

    async def apply_code(self, user: User, code: str) -> str:
        code = code.strip().upper()
        if user.referral_code_applied:
            return 'already'
        result = await self.session.execute(select(ReferralCode).where(ReferralCode.code == code))
        ref = result.scalar_one_or_none()
        if not ref or not ref.active:
            return 'invalid'
        use = ReferralUse(code=ref.code, user_id=user.id, used_at=utcnow())
        ref.usage_count += 1
        user.referral_discount_pct = ref.discount_pct
        user.referral_code_applied = ref.code
        self.session.add(use)
        return 'ok'

    async def list_codes(self) -> list[tuple[str, int, int, bool]]:
        result = await self.session.execute(
            select(ReferralCode.code, ReferralCode.discount_pct, ReferralCode.usage_count, ReferralCode.active)
            .order_by(ReferralCode.created_at.desc())
        )
        return list(result.all())

    async def code_stats(self, code: str) -> int:
        result = await self.session.execute(
            select(func.count(ReferralUse.id)).where(ReferralUse.code == code)
        )
        return int(result.scalar_one() or 0)
