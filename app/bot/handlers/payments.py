from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.keyboards.main import promo_input_menu, topup_menu
from app.bot.states import TopUpFlow
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import Order, PromoCode
from app.services.app_settings import AppSettingsService
from app.services.credits import CreditsService
from app.services.payments import PaymentsService
from app.services.product_pricing import get_product_credits, get_product_stars_price, get_product_usd_price
from app.services.promos import PromoService
from app.services.walletpay import WalletPayClient, WalletPayError
from app.utils.time import utcnow


router = Router()


def _walletpay_enabled(settings) -> bool:
    return bool(settings.walletpay_api_key.strip())


def _walletpay_order_status(value: str | None) -> str:
    return str(value or "").strip().upper()


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


async def send_wallet_options(message: Message, session: AsyncSession) -> bool:
    settings = get_settings()
    lang = get_lang(message.from_user)
    if not _walletpay_enabled(settings):
        await message.answer(t(lang, "walletpay_unavailable"))
        return False

    service = PaymentsService(session)
    products = await service.list_products()
    if not products:
        await message.answer(t(lang, "payment_packages_missing"))
        return False

    settings_service = AppSettingsService(session)
    stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
    usd_per_star = await settings_service.get_float("usd_per_star", 0.013)

    best_id = _best_value_product_id(products)
    buttons = []
    for product in products:
        credits_total = get_product_credits(product)
        usd_price = get_product_usd_price(product, stars_per_credit, usd_per_star)
        label = f"{product.title} - {usd_price} {settings.walletpay_currency.upper()} / {tf(lang, 'payment_desc', credits=credits_total)}"

        bonus = int(product.credits_bonus or 0)
        if bonus > 0:
            label = f"{label} | {tf(lang, 'crypto_bonus_badge', bonus=bonus)}"
        if best_id is not None and int(product.id) == int(best_id):
            label = f"{label} | {t(lang, 'crypto_best_value')}"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=_trim_button_text(label),
                    callback_data=f"pay:wallet:product:{product.id}",
                )
            ]
        )

    await message.answer(t(lang, "walletpay_choose"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
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


@router.callback_query(F.data == "pay:topup:wallet")
async def buy_wallet(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await send_wallet_options(callback.message, session)
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


@router.callback_query(F.data.startswith("pay:wallet:product:"))
async def pay_wallet_product(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        product_id = int((callback.data or "").split(":", 3)[3])
    except (IndexError, ValueError):
        await callback.answer(t(get_lang(callback.from_user), "payment_invalid"), show_alert=True)
        return
    lang = get_lang(callback.from_user)
    settings = get_settings()
    if not _walletpay_enabled(settings):
        await callback.answer(t(lang, "walletpay_unavailable"), show_alert=True)
        return

    service = PaymentsService(session)
    product = await service.get_product(product_id)
    if not product:
        await callback.answer(t(lang, "payment_package_not_found"), show_alert=True)
        return

    credits_service = CreditsService(session)
    user = await credits_service.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t(lang, "payment_user_not_found"), show_alert=True)
        return

    settings_service = AppSettingsService(session)
    stars_per_credit = await settings_service.get_float("stars_per_credit", 2.0)
    usd_per_star = await settings_service.get_float("usd_per_star", 0.013)

    credits_total = get_product_credits(product)
    usd_price = get_product_usd_price(product, stars_per_credit, usd_per_star)
    external_id = f"wallet:{user.id}:{product.id}:{uuid.uuid4().hex[:12]}"

    client = WalletPayClient(
        api_key=settings.walletpay_api_key,
        base_url=settings.walletpay_base_url,
    )
    description = f"{product.title} - {credits_total} credits"
    try:
        preview = await client.create_order(
            amount=usd_price,
            currency_code=settings.walletpay_currency,
            external_id=external_id,
            description=description[:100],
            timeout_seconds=settings.walletpay_timeout_seconds,
            customer_telegram_user_id=user.telegram_id,
            return_url=settings.walletpay_return_url or None,
            fail_return_url=settings.walletpay_fail_return_url or None,
            custom_data=f"user={user.id};product={product.id}",
        )
    except WalletPayError:
        await callback.answer(t(lang, "walletpay_create_failed"), show_alert=True)
        return

    order_id = str(preview.get("id") or "").strip()
    direct_pay_link = str(preview.get("directPayLink") or "").strip()
    if not order_id or not direct_pay_link:
        await callback.answer(t(lang, "walletpay_create_failed"), show_alert=True)
        return

    order = Order(
        user_id=user.id,
        telegram_payment_charge_id=external_id,
        provider_payment_charge_id=order_id,
        payload=f"wallet:{product.id}",
        stars_amount=0,
        credits_amount=credits_total,
        status="wp_active",
        created_at=utcnow(),
    )
    session.add(order)
    await session.commit()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "walletpay_pay_button"), url=direct_pay_link)],
            [InlineKeyboardButton(text=t(lang, "walletpay_check_button"), callback_data=f"pay:wallet:check:{order.id}")],
        ]
    )
    await callback.message.answer(
        tf(
            lang,
            "walletpay_invoice_created",
            amount=str(usd_price),
            currency=settings.walletpay_currency.upper(),
        ),
        reply_markup=keyboard,
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith("pay:wallet:check:"))
async def pay_wallet_check(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        local_order_id = int((callback.data or "").split(":", 3)[3])
    except (IndexError, ValueError):
        await callback.answer(t(get_lang(callback.from_user), "payment_invalid"), show_alert=True)
        return
    lang = get_lang(callback.from_user)
    settings = get_settings()
    if not _walletpay_enabled(settings):
        await callback.answer(t(lang, "walletpay_unavailable"), show_alert=True)
        return

    credits_service = CreditsService(session)
    user = await credits_service.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t(lang, "payment_user_not_found"), show_alert=True)
        return

    order = await session.get(Order, local_order_id)
    if order and order.user_id != user.id:
        order = None
    if order and not (order.payload or "").startswith("wallet:"):
        order = None
    if not order:
        await callback.answer(t(lang, "payment_invalid"), show_alert=True)
        return

    client = WalletPayClient(
        api_key=settings.walletpay_api_key,
        base_url=settings.walletpay_base_url,
    )
    try:
        preview = await client.get_order_preview(order.provider_payment_charge_id)
    except WalletPayError:
        await callback.answer(t(lang, "walletpay_status_failed"), show_alert=True)
        return

    order_status = _walletpay_order_status(preview.get("status"))
    if order_status == "PAID":
        payments = PaymentsService(session)
        paid_now, credited = await payments.settle_walletpay_order(order)
        await session.commit()
        if paid_now:
            await callback.message.answer(tf(lang, "payment_success", credits=credited))
        else:
            await callback.message.answer(t(lang, "payment_processed"))
        await callback.answer()
        return

    order.status = f"wp_{order_status.lower()}"[:32] if order_status else order.status
    await session.commit()
    if order_status in {"CANCELLED", "EXPIRED"}:
        await callback.answer(
            tf(lang, "walletpay_canceled", status=order_status),
            show_alert=True,
        )
    else:
        await callback.answer(
            tf(lang, "walletpay_waiting", status=order_status or "ACTIVE"),
            show_alert=True,
        )


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
