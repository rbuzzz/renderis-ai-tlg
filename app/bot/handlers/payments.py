from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.payments import PaymentsService
from app.services.credits import CreditsService
from app.services.product_pricing import get_product_credits, get_product_stars_price
from app.bot.utils import safe_cleanup_callback
from app.bot.i18n import get_lang, t, tf


router = Router()


async def send_buy_options(message: Message, session: AsyncSession) -> bool:
    service = PaymentsService(session)
    products = await service.list_products()
    lang = get_lang(message.from_user)
    if not products:
        await message.answer(t(lang, "payment_packages_missing"))
        return False

    buttons = []
    for p in products:
        stars_price = get_product_stars_price(p)
        credits_total = get_product_credits(p)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{p.title} - {stars_price} звезд / {credits_total} кр.",
                    callback_data=f"pay:product:{p.id}",
                )
            ]
        )
    await message.answer(t(lang, "payment_choose"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    return True


@router.callback_query(F.data == 'pay:buy')
async def buy_credits(callback: CallbackQuery, session: AsyncSession) -> None:
    await send_buy_options(callback.message, session)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('pay:product:'))
async def pay_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(':', 2)[2])
    service = PaymentsService(session)
    product = await service.get_product(product_id)
    lang = get_lang(callback.from_user)
    if not product:
        await callback.answer(t(lang, "payment_package_not_found"), show_alert=True)
        return

    payload = f'stars:{product.id}:{uuid.uuid4()}'
    credits_total = get_product_credits(product)
    stars_price = get_product_stars_price(product)
    prices = [LabeledPrice(label=product.title, amount=stars_price)]
    settings = get_settings()

    await callback.message.answer_invoice(
        title=product.title,
        description=tf(lang, "payment_desc", credits=credits_total),
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
    lang = get_lang(message.from_user)
    if len(parts) < 2:
        await message.answer(t(lang, "payment_invalid"))
        return
    product_id = int(parts[1])
    service = PaymentsService(session)
    product = await service.get_product(product_id)
    if not product:
        await message.answer(t(lang, "payment_package_not_found"))
        return
    credits_total = get_product_credits(product)
    stars_price = get_product_stars_price(product)

    credits_service = CreditsService(session)
    user = await credits_service.get_user(message.from_user.id)
    if not user:
        await message.answer(t(lang, "payment_user_not_found"))
        return

    ok = await service.record_successful_payment(
        user,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
        payload,
        stars_price,
        credits_total,
    )
    await session.commit()

    if not ok:
        await message.answer(t(lang, "payment_processed"))
        return

    await message.answer(tf(lang, "payment_success", credits=credits_total))
