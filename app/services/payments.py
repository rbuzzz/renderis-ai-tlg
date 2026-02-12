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

    async def settle_cryptopay_order(self, order: Order) -> tuple[bool, int]:
        if order.status == "paid":
            return False, 0

        user = await self.session.get(User, order.user_id)
        if not user:
            return False, 0

        credits_total = int(order.credits_amount or 0)
        if credits_total <= 0:
            return False, 0

        credits = CreditsService(self.session)
        await credits.add_ledger(
            user,
            credits_total,
            "purchase",
            meta={
                "provider": "cryptopay",
                "invoice_id": order.provider_payment_charge_id,
                "payload": order.payload,
            },
            idempotency_key=f"cp_invoice:{order.provider_payment_charge_id}",
        )
        order.status = "paid"
        return True, credits_total
