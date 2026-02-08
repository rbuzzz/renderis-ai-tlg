from __future__ import annotations

from aiogram.filters import Command
from aiogram.types import Message
from aiogram import Router
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.db.models import PromoCode
from app.services.credits import CreditsService
from app.services.promos import PromoService
from app.services.referrals import ReferralService


router = Router()


@router.message(Command('ref'))
async def apply_ref(message: Message, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    lang = get_lang(message.from_user)
    if len(parts) < 2:
        await message.answer(t(lang, "ref_usage"))
        return
    code = parts[1]
    credits = CreditsService(session)
    user = await credits.get_user(message.from_user.id)
    if not user:
        await message.answer(t(lang, "history_user_not_found"))
        return
    service = ReferralService(session)
    status = await service.apply_code(user, code)
    await session.commit()

    if status == 'already':
        await message.answer(t(lang, "ref_already"))
    elif status == 'invalid':
        await message.answer(t(lang, "ref_not_found"))
    else:
        await message.answer(t(lang, "ref_applied"))


@router.message(Command('promo'))
async def apply_promo(message: Message, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    lang = get_lang(message.from_user)
    if len(parts) < 2:
        await message.answer(t(lang, "promo_usage"))
        return
    code = parts[1]
    credits = CreditsService(session)
    user = await credits.get_user(message.from_user.id)
    if not user:
        await message.answer(t(lang, "history_user_not_found"))
        return
    service = PromoService(session)
    status = await service.redeem(user, code)
    if status == 'invalid':
        await message.answer(t(lang, "promo_invalid"))
        return
    if status == 'used':
        await message.answer(t(lang, "promo_used"))
        return

    promo_row = await session.get(PromoCode, code.strip().upper())
    if not promo_row:
        await message.answer(t(lang, "promo_not_found"))
        return

    await credits.add_ledger(
        user,
        promo_row.credits_amount,
        'promo_redeem',
        meta={'code': promo_row.code},
        idempotency_key=f'promo:{promo_row.code}:{user.id}',
    )
    await session.commit()
    await message.answer(tf(lang, "promo_activated", credits=promo_row.credits_amount))
