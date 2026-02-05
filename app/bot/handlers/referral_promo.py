from __future__ import annotations

from aiogram.filters import Command
from aiogram.types import Message
from aiogram import Router
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PromoCode
from app.services.credits import CreditsService
from app.services.promos import PromoService
from app.services.referrals import ReferralService


router = Router()


@router.message(Command('ref'))
async def apply_ref(message: Message, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    if len(parts) < 2:
        await message.answer('Использование: /ref CODE')
        return
    code = parts[1]
    credits = CreditsService(session)
    user = await credits.get_user(message.from_user.id)
    if not user:
        await message.answer('Пользователь не найден.')
        return
    service = ReferralService(session)
    status = await service.apply_code(user, code)
    await session.commit()

    if status == 'already':
        await message.answer('Реферальный код уже применён ранее.')
    elif status == 'invalid':
        await message.answer('Код не найден или неактивен.')
    else:
        await message.answer('Реферальный код применён. Скидка будет учтена в генерациях.')


@router.message(Command('promo'))
async def apply_promo(message: Message, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    if len(parts) < 2:
        await message.answer('Использование: /promo CODE')
        return
    code = parts[1]
    credits = CreditsService(session)
    user = await credits.get_user(message.from_user.id)
    if not user:
        await message.answer('Пользователь не найден.')
        return
    service = PromoService(session)
    status = await service.redeem(user, code)
    if status == 'invalid':
        await message.answer('Промо-код не найден или неактивен.')
        return
    if status == 'used':
        await message.answer('Промо-код уже использован.')
        return

    promo_row = await session.get(PromoCode, code.strip().upper())
    if not promo_row:
        await message.answer('Промо-код не найден.')
        return

    await credits.add_ledger(
        user,
        promo_row.credits_amount,
        'promo_redeem',
        meta={'code': promo_row.code},
        idempotency_key=f'promo:{promo_row.code}:{user.id}',
    )
    await session.commit()
    await message.answer(f'Промо-код активирован. Начислено {promo_row.credits_amount} кредитов.')
