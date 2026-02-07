from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Price
from app.modelspecs.base import ModelSpec


@dataclass
class PriceBreakdown:
    base: int
    modifiers: List[Tuple[str, int]]
    per_output: int
    outputs: int
    discount_pct: int
    total: int


class PricingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_price_map(self, model_key: str) -> Dict[str, int]:
        result = await self.session.execute(
            select(Price.option_key, Price.price_credits)
            .where(Price.model_key == model_key)
            .where(Price.active.is_(True))
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def get_provider_map(self, model_key: str) -> Dict[str, int]:
        result = await self.session.execute(
            select(Price.option_key, Price.provider_credits)
            .where(Price.model_key == model_key)
            .where(Price.active.is_(True))
        )
        data: Dict[str, int] = {}
        for key, value in result.all():
            if value is None:
                continue
            data[key] = int(value)
        return data

    async def resolve_provider_credits(
        self, model: ModelSpec, options: Dict[str, str], outputs: int
    ) -> int:
        provider_map = await self.get_provider_map(model.key)
        if model.key == "nano_banana_pro":
            refs = options.get("reference_images", "none")
            resolution = options.get("resolution", "1K")
            if refs == "has":
                bundle_key = f"bundle_refs_{resolution.lower()}"
                if bundle_key in provider_map:
                    return provider_map[bundle_key] * outputs
            else:
                bundle_key = f"bundle_no_refs_{resolution.lower()}"
                if bundle_key in provider_map:
                    return provider_map[bundle_key] * outputs
        base = provider_map.get("base", 0)
        modifiers: List[int] = []
        for opt in model.options:
            val = options.get(opt.key, opt.default)
            price_key = None
            for v in opt.values:
                if v.value == val:
                    price_key = v.price_key
                    break
            if price_key and price_key in provider_map:
                if price_key.startswith("output_format_") or price_key.startswith("aspect_"):
                    continue
                if model.key == "nano_banana_pro" and opt.key == "reference_images" and val == "none":
                    continue
                modifiers.append(provider_map[price_key])
        per_output = base + sum(modifiers)
        return per_output * outputs

    async def resolve_cost(self, model: ModelSpec, options: Dict[str, str], outputs: int, discount_pct: int = 0) -> PriceBreakdown:
        price_map = await self.get_price_map(model.key)
        if model.key == "nano_banana_pro":
            refs = options.get("reference_images", "none")
            resolution = options.get("resolution", "1K")
            if refs == "has":
                bundle_key = f"bundle_refs_{resolution.lower()}"
                if bundle_key in price_map:
                    per_output = price_map[bundle_key]
                    subtotal = per_output * outputs
                    if discount_pct > 0:
                        total = int((subtotal * (100 - discount_pct) + 99) // 100)
                    else:
                        total = subtotal
                    return PriceBreakdown(
                        base=per_output,
                        modifiers=[],
                        per_output=per_output,
                        outputs=outputs,
                        discount_pct=discount_pct,
                        total=total,
                    )
            else:
                bundle_key = f"bundle_no_refs_{resolution.lower()}"
                if bundle_key in price_map:
                    per_output = price_map[bundle_key]
                    subtotal = per_output * outputs
                    if discount_pct > 0:
                        total = int((subtotal * (100 - discount_pct) + 99) // 100)
                    else:
                        total = subtotal
                    return PriceBreakdown(
                        base=per_output,
                        modifiers=[],
                        per_output=per_output,
                        outputs=outputs,
                        discount_pct=discount_pct,
                        total=total,
                    )
        base = price_map.get('base', 0)
        modifiers: List[Tuple[str, int]] = []
        for opt in model.options:
            val = options.get(opt.key, opt.default)
            price_key = None
            for v in opt.values:
                if v.value == val:
                    price_key = v.price_key
                    break
            if price_key and price_key in price_map:
                if price_key.startswith("output_format_") or price_key.startswith("aspect_"):
                    continue
                if model.key == "nano_banana_pro" and opt.key == "reference_images" and val == "none":
                    continue
                modifiers.append((price_key, price_map[price_key]))
        per_output = base + sum(x[1] for x in modifiers)
        subtotal = per_output * outputs
        if discount_pct > 0:
            total = int((subtotal * (100 - discount_pct) + 99) // 100)
        else:
            total = subtotal
        return PriceBreakdown(
            base=base,
            modifiers=modifiers,
            per_output=per_output,
            outputs=outputs,
            discount_pct=discount_pct,
            total=total,
        )

    async def set_price(self, model_key: str, option_key: str, price_credits: int, model_type: str, provider: str) -> None:
        result = await self.session.execute(
            select(Price).where(Price.model_key == model_key, Price.option_key == option_key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.price_credits = price_credits
            row.active = True
        else:
            self.session.add(
                Price(
                    model_key=model_key,
                    option_key=option_key,
                    price_credits=price_credits,
                    active=True,
                    model_type=model_type,
                    provider=provider,
                )
            )

    async def bulk_multiply(self, multiplier: float) -> int:
        result = await self.session.execute(select(func.count(Price.id)))
        count = result.scalar_one() or 0
        await self.session.execute(
            update(Price).values(price_credits=func.round(Price.price_credits * multiplier))
        )
        return int(count)
