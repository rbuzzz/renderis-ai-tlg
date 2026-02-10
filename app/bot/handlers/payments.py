from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.keyboards.main import promo_input_menu, topup_menu
from app.bot.states import TopUpFlow
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import PromoCode
from app.services.credits import CreditsService
from app.services.payments import PaymentsService
from app.services.product_pricing import get_product_credits, get_product_stars_price
from app.services.promos import PromoService


router = Router()


def _trim_button_text(text: str, max_len: int = 64) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _stars_per_credit_milli(stars_price: int, credits_total: int) -> int:
    if stars_price <= 0 or credits_total <= 0:
        return 0
    value = (Decimal(stars_price) * Decimal("1000") / Decimal(credits_total)).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(value)


def _best_value_product_id(products: list) -> int | None:
    best: tuple[int, int, int, int] | None = None
    for product in products:
        stars_price = get_product_stars_price(product)
        credits_total = get_product_credits(product)
        rate = _stars_per_credit_milli(stars_price, credits_total)
        if rate <= 0:
            continue
        candidate = (
            rate,
            -credits_total,
            int(getattr(product, "sort_order", 0) or 0),
            int(product.id),
        )
        if best is None or candidate < best:
            best = candidate
    return best[3] if best else None


def _payment_description(lang: str, product) -> str:
    credits_total = get_product_credits(product)
    description = tf(lang, "payment_desc", credits=credits_total)
    bonus = int(product.credits_bonus or 0)
    if bonus <= 0:
        return description
    base = int(product.credits_base if product.credits_base is not None else product.credits_amount)
    return f"{description}. {tf(lang, 'crypto_base_bonus_line', base=base, bonus=bonus)}"


async def send_topup_options(message: Message) -> None:
    lang = get_lang(message.from_user)
    await message.answer(t(lang, "payment_topup_choose"), reply_markup=topup_menu(lang))


async def send_buy_options(message: Message, session: AsyncSession) -> bool:
    service = PaymentsService(session)
    products = await service.list_products()
    lang = get_lang(message.from_user)
    if not products:
        await message.answer(t(lang, "payment_packages_missing"))
        return False

    best_id = _best_value_product_id(products)
    buttons = []
    for product in products:
        stars_price = get_product_stars_price(product)
        credits_total = get_product_credits(product)
        label = f"{product.title} - {stars_price}⭐ / {tf(lang, 'payment_desc', credits=credits_total)}"

        bonus = int(product.credits_bonus or 0)
        if bonus > 0:
            label = f"{label} | {tf(lang, 'crypto_bonus_badge', bonus=bonus)}"
        if best_id is not None and int(product.id) == int(best_id):
            label = f"{label} | {t(lang, 'crypto_best_value')}"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=_trim_button_text(label),
                    callback_data=f"pay:product:{product.id}",
                )
            ]
        )

    await message.answer(t(lang, "payment_choose"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    return True


@router.callback_query(F.data == "pay:buy")
async def buy_credits(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await send_topup_options(callback.message)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "pay:topup:stars")
async def buy_stars(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await send_buy_options(callback.message, session)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "pay:topup:promo")
async def buy_promo(callback: CallbackQuery, state: FSMContext) -> None:
    lang = get_lang(callback.from_user)
    await state.set_state(TopUpFlow.entering_promo_code)
    await callback.message.answer(t(lang, "payment_promo_enter"), reply_markup=promo_input_menu(lang))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "pay:topup:promo:cancel")
async def buy_promo_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    lang = get_lang(callback.from_user)
    await state.clear()
    await callback.message.answer(t(lang, "payment_promo_cancelled"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(TopUpFlow.entering_promo_code, F.text)
async def promo_code_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    lang = get_lang(message.from_user)
    raw = (message.text or "").strip()
    if not raw:
        await message.answer(t(lang, "payment_promo_enter"), reply_markup=promo_input_menu(lang))
        return

    if raw.lower() in {"/cancel", "cancel"}:
        await state.clear()
        await message.answer(t(lang, "payment_promo_cancelled"))
        return

    if raw.startswith("/") or " " in raw:
        await message.answer(t(lang, "payment_promo_enter"), reply_markup=promo_input_menu(lang))
        return

    code = raw.upper()
    credits = CreditsService(session)
    user = await credits.get_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer(t(lang, "history_user_not_found"))
        return

    service = PromoService(session)
    status = await service.redeem(user, code)
    if status == "invalid":
        await message.answer(t(lang, "promo_invalid"), reply_markup=promo_input_menu(lang))
        return
    if status == "used":
        await message.answer(t(lang, "promo_used"), reply_markup=promo_input_menu(lang))
        return

    promo_row = await session.get(PromoCode, code)
    if not promo_row:
        await message.answer(t(lang, "promo_not_found"), reply_markup=promo_input_menu(lang))
        return

    await credits.add_ledger(
        user,
        promo_row.credits_amount,
        "promo_redeem",
        meta={"code": promo_row.code},
        idempotency_key=f"promo:{promo_row.code}:{user.id}",
    )
    await session.commit()
    await state.clear()
    await message.answer(tf(lang, "promo_activated", credits=promo_row.credits_amount))


@router.message(TopUpFlow.entering_promo_code)
async def promo_code_input_invalid(message: Message) -> None:
    lang = get_lang(message.from_user)
    await message.answer(t(lang, "payment_promo_enter"), reply_markup=promo_input_menu(lang))


@router.callback_query(F.data.startswith("pay:product:"))
async def pay_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":", 2)[2])
    service = PaymentsService(session)
    product = await service.get_product(product_id)
    lang = get_lang(callback.from_user)
    if not product:
        await callback.answer(t(lang, "payment_package_not_found"), show_alert=True)
        return

    payload = f"stars:{product.id}:{uuid.uuid4()}"
    credits_total = get_product_credits(product)
    stars_price = get_product_stars_price(product)
    prices = [LabeledPrice(label=product.title, amount=stars_price)]
    settings = get_settings()

    await callback.message.answer_invoice(
        title=product.title,
        description=_payment_description(lang, product),
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
    parts = payload.split(":")
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
