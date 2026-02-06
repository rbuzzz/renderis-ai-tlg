from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.payments import PaymentsService
from app.services.credits import CreditsService
from app.bot.utils import safe_cleanup_callback


router = Router()


@router.callback_query(F.data == 'pay:buy')
async def buy_credits(callback: CallbackQuery, session: AsyncSession) -> None:
    service = PaymentsService(session)
    products = await service.list_products()
    if not products:
        await callback.message.answer('⚠️ Пакеты не настроены. Обратитесь к администратору.')
        await callback.answer()
        await safe_cleanup_callback(callback)
        return
    buttons = []
    for p in products:
        buttons.append([
            InlineKeyboardButton(
                text=f'{p.title} - {p.stars_amount} звезд',
                callback_data=f'pay:product:{p.id}',
            )
        ])
    await callback.message.answer('💳 Выберите пакет:', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('pay:product:'))
async def pay_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(':', 2)[2])
    service = PaymentsService(session)
    product = await service.get_product(product_id)
    if not product:
        await callback.answer('Пакет не найден', show_alert=True)
        return

    payload = f'stars:{product.id}:{uuid.uuid4()}'
    prices = [LabeledPrice(label=product.title, amount=product.stars_amount)]
    settings = get_settings()

    await callback.message.answer_invoice(
        title=product.title,
        description=f'{product.credits_amount} кредитов',
        payload=payload,
        provider_token=settings.stars_provider_token,
        currency=settings.stars_currency,
        prices=prices,
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.pre_checkout_query()
async def pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, session: AsyncSession) -> None:
    payment = message.successful_payment
    payload = payment.invoice_payload
    parts = payload.split(':')
    if len(parts) < 2:
        await message.answer('Некорректный платеж.')
        return
    product_id = int(parts[1])
    service = PaymentsService(session)
    product = await service.get_product(product_id)
    if not product:
        await message.answer('Пакет не найден.')
        return

    credits_service = CreditsService(session)
    user = await credits_service.get_user(message.from_user.id)
    if not user:
        await message.answer('Пользователь не найден.')
        return

    ok = await service.record_successful_payment(
        user,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
        payload,
        product.stars_amount,
        product.credits_amount,
    )
    await session.commit()

    if not ok:
        await message.answer('Платеж уже обработан.')
        return

    await message.answer(f'Оплата принята. Начислено {product.credits_amount} кредитов.')
