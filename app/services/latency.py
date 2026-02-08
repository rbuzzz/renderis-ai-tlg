from __future__ import annotations

from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelLatencyStat
from app.utils.time import utcnow


class ModelLatencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_avg_seconds(self, model_key: str) -> Optional[float]:
        stat = await self.session.get(ModelLatencyStat, model_key)
        if not stat:
            return None
        try:
            return float(stat.avg_seconds or 0)
        except Exception:
            return None

    async def get_all(self) -> Dict[str, float]:
        result = await self.session.execute(select(ModelLatencyStat))
        stats = result.scalars().all()
        out: Dict[str, float] = {}
        for stat in stats:
            try:
                out[stat.model_key] = float(stat.avg_seconds or 0)
            except Exception:
                continue
        return out

    async def update(self, model_key: str, duration_seconds: float) -> None:
        if duration_seconds <= 0:
            return
        stat = await self.session.get(ModelLatencyStat, model_key)
        if not stat:
            stat = ModelLatencyStat(
                model_key=model_key,
                avg_seconds=round(duration_seconds, 2),
                sample_count=1,
                updated_at=utcnow(),
            )
            self.session.add(stat)
            return

        count = int(stat.sample_count or 0)
        new_count = count + 1
        try:
            current_avg = float(stat.avg_seconds or 0)
        except Exception:
            current_avg = 0.0
        new_avg = ((current_avg * count) + duration_seconds) / new_count
        stat.avg_seconds = round(new_avg, 2)
        stat.sample_count = new_count
        stat.updated_at = utcnow()
