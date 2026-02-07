from __future__ import annotations

from typing import Optional, Tuple

from app.services.app_settings import AppSettingsService


class KieBalanceService:
    def __init__(self, session) -> None:
        self.session = session
        self.settings = AppSettingsService(session)

    async def get_balance(self) -> int:
        raw = await self.settings.get("kie_balance_credits", "0")
        try:
            return int(float(raw or 0))
        except ValueError:
            return 0

    async def add_credits(self, amount: int) -> int:
        if amount <= 0:
            return await self.get_balance()
        current = await self.get_balance()
        new_balance = current + amount
        await self.settings.set("kie_balance_credits", str(new_balance))
        await self._update_level(new_balance)
        return new_balance

    async def spend_credits(self, amount: int) -> Tuple[str, int, int, int, int, float] | None:
        if amount <= 0:
            return None
        current = await self.get_balance()
        new_balance = current - amount
        await self.settings.set("kie_balance_credits", str(new_balance))
        return await self._maybe_alert(new_balance)

    async def _get_thresholds(self) -> Tuple[int, int, int]:
        green = await self._get_int("kie_warn_green", 1000)
        yellow = await self._get_int("kie_warn_yellow", 500)
        red = await self._get_int("kie_warn_red", 200)
        return green, yellow, red

    async def _get_int(self, key: str, default: int) -> int:
        raw = await self.settings.get(key, str(default))
        try:
            return int(float(raw or default))
        except ValueError:
            return default

    async def _update_level(self, balance: int) -> None:
        level = await self._calc_level(balance)
        await self.settings.set("kie_warn_level", level)

    async def _calc_level(self, balance: int) -> str:
        green, yellow, red = await self._get_thresholds()
        if balance <= red:
            return "red"
        if balance <= yellow:
            return "yellow"
        if balance <= green:
            return "green"
        return "ok"

    async def _maybe_alert(self, balance: int) -> Optional[Tuple[str, int, int, int, int, float]]:
        green, yellow, red = await self._get_thresholds()
        level = await self._calc_level(balance)
        last = await self.settings.get("kie_warn_level", "ok") or "ok"
        order = {"ok": 0, "green": 1, "yellow": 2, "red": 3}
        await self.settings.set("kie_warn_level", level)
        if order.get(level, 0) <= order.get(last, 0):
            return None
        usd_per_credit = await self.settings.get_float("kie_usd_per_credit", 0.02)
        return level, balance, green, yellow, red, usd_per_credit
