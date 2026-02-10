from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.db.models import StarProduct


def _quantize_usd(value: Decimal) -> Decimal:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded <= Decimal("0"):
        return Decimal("0.01")
    return rounded


def get_product_credits(product: StarProduct) -> int:
    base = int(product.credits_base if product.credits_base is not None else product.credits_amount)
    bonus = int(product.credits_bonus or 0)
    total = base + bonus
    if total > 0:
        return total
    return int(product.credits_amount)


def get_product_stars_price(product: StarProduct) -> int:
    explicit = product.price_stars
    if explicit is not None and int(explicit) > 0:
        return int(explicit)
    return int(product.stars_amount)


def credits_to_usd(credits_amount: int, stars_per_credit: float, usd_per_star: float) -> Decimal:
    usd_per_credit = Decimal(str(stars_per_credit)) * Decimal(str(usd_per_star))
    total = Decimal(str(credits_amount)) * usd_per_credit
    return _quantize_usd(total)


def get_product_usd_price(product: StarProduct, stars_per_credit: float, usd_per_star: float) -> Decimal:
    if product.price_usd is not None:
        explicit = Decimal(str(product.price_usd))
        if explicit > 0:
            return _quantize_usd(explicit)
    return credits_to_usd(get_product_credits(product), stars_per_credit, usd_per_star)
