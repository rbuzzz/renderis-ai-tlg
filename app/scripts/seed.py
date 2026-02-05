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
    ('nano_banana', 'aspect_3_4', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_4_3', 1, 'image', 'kie'),
    ('nano_banana', 'aspect_9_16', 2, 'image', 'kie'),
    ('nano_banana', 'aspect_16_9', 2, 'image', 'kie'),
    # Nano Banana Pro
    ('nano_banana_pro', 'base', 10, 'image', 'kie'),
    ('nano_banana_pro', 'output_format_png', 0, 'image', 'kie'),
    ('nano_banana_pro', 'output_format_jpg', 0, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_1_1', 0, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_3_4', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_4_3', 2, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_9_16', 3, 'image', 'kie'),
    ('nano_banana_pro', 'aspect_16_9', 3, 'image', 'kie'),
    ('nano_banana_pro', 'resolution_1k', 0, 'image', 'kie'),
    ('nano_banana_pro', 'resolution_2k', 5, 'image', 'kie'),
    ('nano_banana_pro', 'resolution_4k', 10, 'image', 'kie'),
    ('nano_banana_pro', 'ref_none', 0, 'image', 'kie'),
    ('nano_banana_pro', 'ref_has', 5, 'image', 'kie'),
]

DEFAULT_PRODUCTS = [
    ('Стартовый', 100, 50, 1),
    ('Популярный', 250, 150, 2),
    ('Профи', 700, 500, 3),
]


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        for model_key, option_key, price, model_type, provider in DEFAULT_PRICES:
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
                    active=True,
                    model_type=model_type,
                    provider=provider,
                )
            )

        for title, stars, credits, order in DEFAULT_PRODUCTS:
            result = await session.execute(select(StarProduct).where(StarProduct.title == title))
            if result.scalar_one_or_none():
                continue
            session.add(
                StarProduct(
                    title=title,
                    stars_amount=stars,
                    credits_amount=credits,
                    active=True,
                    sort_order=order,
                )
            )

        await session.commit()

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
