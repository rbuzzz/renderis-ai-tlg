from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


CREDIT_PRECISION = Decimal("0.001")


def to_credits(value: Any, default: Decimal | str = "0") -> Decimal:
    if value is None:
        base = Decimal(str(default))
        return base.quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)

    if isinstance(value, Decimal):
        return value.quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)

    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)

    if isinstance(value, str):
        raw = value.strip().replace(",", ".")
        if not raw:
            return Decimal(str(default)).quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)
        try:
            return Decimal(raw).quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return Decimal(str(default)).quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)

    try:
        return Decimal(str(value)).quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default)).quantize(CREDIT_PRECISION, rounding=ROUND_HALF_UP)


def credits_to_display(value: Any) -> str:
    dec = to_credits(value)
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
