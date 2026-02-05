from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


def create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = create_engine()
    return async_sessionmaker(engine, expire_on_commit=False)
