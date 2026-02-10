from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Price, StarProduct


DEFAULT_PRICES = [
    # Nano Banana
    ('nano_banana', 'base', 5, 'image', 'kie'),
    ('nano_banana', 'output_format_png', 0, 'image', 'kie'),
    ('nano_banana', 'output_format_jpeg', 0, 'image', 'kie'),
    ('nano_banana', 'aspect_1_1', 0, 'image', 'kie'),
    ('nano_banana', 'aspect_2_3', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_3_4', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_3_2', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_4_3', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_4_5', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_5_4', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_9_16', 2, 'image', 'kie'),
    ('nano_banana', 'aspect_16_9', 2, 'image', 'kie'),
    ('nano_banana', 'aspect_21_9', 2, 'image', 'kie'),
    ('nano_banana', 'aspect_auto', 0, 'image', 'kie'),
    # Nano Banana Pro
    ('nano_banana_pro', 'base', 10, 'image', 'kie'),
    ('nano_banana_pro', 'output_format_png', 0, 'image', 'kie'),
    ('nano_banana_pro', 'output_format_jpg', 0, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_1_1', 0, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_2_3', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_3_4', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_3_2', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_4_3', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_4_5', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_5_4', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_9_16', 3, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_16_9', 3, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_21_9', 3, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_auto', 0, 'image', 'kie'),
    ('nano_banana_pro', 'resolution_1k', 0, 'image', 'kie'),
    ('nano_banana_pro', 'resolution_2k', 5, 'image', 'kie'),
    ('nano_banana_pro', 'resolution_4k', 10, 'image', 'kie'),
    ('nano_banana_pro', 'ref_none', 0, 'image', 'kie'),
    ('nano_banana_pro', 'ref_has', 5, 'image', 'kie'),
    # Nano Banana Edit
    ('nano_banana_edit', 'base', 8, 'image', 'kie'),
    ('nano_banana_edit', 'output_format_png', 0, 'image', 'kie'),
    ('nano_banana_edit', 'output_format_jpeg', 0, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_1_1', 0, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_2_3', 1, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_3_4', 1, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_3_2', 1, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_4_3', 1, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_4_5', 1, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_5_4', 1, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_9_16', 2, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_16_9', 2, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_21_9', 2, 'image', 'kie'),
    ('nano_banana_edit', 'aspect_auto', 0, 'image', 'kie'),
]

DEFAULT_PRODUCTS = [
    ('50 кредитов', 100, 50, 1),
    ('100 кредитов', 200, 100, 2),
    ('250 кредитов', 500, 250, 3),
    ('500 кредитов', 1000, 500, 4),
]


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        for item in DEFAULT_PRICES:
            model_key, option_key, price, model_type, provider = item[:5]
            provider_credits = item[5] if len(item) > 5 else None
            provider_cost_usd = item[6] if len(item) > 6 else None
            result = await session.execute(
                select(Price).where(Price.model_key == model_key, Price.option_key == option_key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                continue
            session.add(
                Price(
                    model_key=model_key,
                    option_key=option_key,
                    price_credits=price,
                    provider_credits=provider_credits,
                    provider_cost_usd=provider_cost_usd,
                    active=True,
                    model_type=model_type,
                    provider=provider,
                )
            )

        allowed_titles = {title for title, _, _, _ in DEFAULT_PRODUCTS}
        for title, stars, credits, order in DEFAULT_PRODUCTS:
            result = await session.execute(select(StarProduct).where(StarProduct.title == title))
            existing = result.scalar_one_or_none()
            if existing:
                existing.stars_amount = stars
                existing.credits_amount = credits
                existing.credits_base = credits
                existing.credits_bonus = 0
                existing.price_stars = None
                existing.price_usd = None
                existing.active = True
                existing.sort_order = order
                continue
            session.add(
                StarProduct(
                    title=title,
                    stars_amount=stars,
                    credits_amount=credits,
                    credits_base=credits,
                    credits_bonus=0,
                    price_stars=None,
                    price_usd=None,
                    active=True,
                    sort_order=order,
                )
            )
        extra_products = await session.execute(
            select(StarProduct).where(StarProduct.title.not_in(allowed_titles))
        )
        for row in extra_products.scalars().all():
            row.active = False

        await session.commit()

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
