from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, StarProduct, User
from app.services.credits import CreditsService
from app.utils.time import utcnow


class PaymentsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_products(self) -> list[StarProduct]:
        result = await self.session.execute(
            select(StarProduct).where(StarProduct.active.is_(True)).order_by(StarProduct.sort_order)
        )
        return list(result.scalars().all())

    async def get_product(self, product_id: int) -> StarProduct | None:
        result = await self.session.execute(select(StarProduct).where(StarProduct.id == product_id))
        return result.scalar_one_or_none()

    async def record_successful_payment(
        self,
        user: User,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str,
        payload: str,
        stars_amount: int,
        credits_amount: int,
    ) -> bool:
        existing = await self.session.execute(
            select(Order).where(Order.telegram_payment_charge_id == telegram_payment_charge_id)
        )
        if existing.scalar_one_or_none():
            return False
        order = Order(
            user_id=user.id,
            telegram_payment_charge_id=telegram_payment_charge_id,
            provider_payment_charge_id=provider_payment_charge_id,
            payload=payload,
            stars_amount=stars_amount,
            credits_amount=credits_amount,
            status='paid',
            created_at=utcnow(),
        )
        self.session.add(order)

        credits = CreditsService(self.session)
        await credits.add_ledger(
            user,
            credits_amount,
            'purchase',
            meta={'stars': stars_amount, 'payload': payload, 'order_id': telegram_payment_charge_id},
            idempotency_key=f'order:{telegram_payment_charge_id}',
        )
        return True
