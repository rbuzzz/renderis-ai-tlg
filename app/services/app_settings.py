from __future__ import annotations

from typing import Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting
from app.utils.time import utcnow


DEFAULT_SETTINGS: Dict[str, str] = {
    'stars_per_credit': '2',
    'usd_per_star': '0.013',
    'kie_usd_per_credit': '0.02',
    'kie_balance_credits': '0',
    'kie_warn_green': '1000',
    'kie_warn_yellow': '500',
    'kie_warn_red': '200',
    'kie_warn_level': 'ok',
}


class AppSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str, default: str | None = None) -> str | None:
        result = await self.session.execute(select(AppSetting).where(AppSetting.key == key))
        row = result.scalar_one_or_none()
        if row:
            return row.value
        if default is None:
            default = DEFAULT_SETTINGS.get(key)
        if default is None:
            return None
        self.session.add(AppSetting(key=key, value=str(default), updated_at=utcnow()))
        await self.session.flush()
        return str(default)

    async def set(self, key: str, value: str) -> None:
        result = await self.session.execute(select(AppSetting).where(AppSetting.key == key))
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = utcnow()
        else:
            self.session.add(AppSetting(key=key, value=value, updated_at=utcnow()))

    async def get_float(self, key: str, default: float) -> float:
        raw = await self.get(key, str(default))
        try:
            return float(raw) if raw is not None else default
        except ValueError:
            return default
