from __future__ import annotations

import random
import string
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PromoCode, User
from app.utils.time import utcnow


class PromoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _generate_code(self, length: int = 12) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return ''.join(random.choice(alphabet) for _ in range(length))

    async def create_batch(self, amount: int, credits: int, admin_id: int, batch_id: str) -> List[PromoCode]:
        codes: List[PromoCode] = []
        for _ in range(amount):
            code = self._generate_code()
            promo = PromoCode(
                code=code,
                credits_amount=credits,
                created_by_admin_id=admin_id,
                created_at=utcnow(),
                active=True,
                batch_id=batch_id,
            )
            self.session.add(promo)
            codes.append(promo)
        return codes

    async def redeem(self, user: User, code: str) -> str:
        code = code.strip().upper()
        result = await self.session.execute(select(PromoCode).where(PromoCode.code == code))
        promo = result.scalar_one_or_none()
        if not promo or not promo.active:
            return 'invalid'
        if promo.redeemed_by_user_id:
            return 'used'
        promo.redeemed_by_user_id = user.id
        promo.redeemed_at = utcnow()
        return 'ok'
