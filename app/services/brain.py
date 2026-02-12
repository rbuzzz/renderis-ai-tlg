from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIBrainConfig, AIBrainLog, AIImprovementBalance, User
from app.services.credits import CreditsService
from app.utils.time import utcnow


DEFAULT_SYSTEM_PROMPT = (
    "You are a professional AI prompt engineer. Improve the user's prompt to be more detailed, "
    "cinematic, structured, and optimized for image generation models. "
    "Do not add unrelated concepts. Keep original intent."
)


class BrainProviderError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass
class BrainChargeResult:
    source: str
    spent_credits: int
    remaining_improvements: int
    balance_credits: int


class AIBrainService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        openai_api_key: str = "",
        openai_base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 45.0,
    ) -> None:
        self.session = session
        self.openai_api_key = (openai_api_key or "").strip()
        self.openai_base_url = (openai_base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout_seconds = max(5.0, float(timeout_seconds))

    async def get_config(self) -> AIBrainConfig:
        row = await self.session.get(AIBrainConfig, 1)
        if row:
            return row

        now = utcnow()
        row = AIBrainConfig(
            id=1,
            enabled=False,
            openai_model="gpt-4o-mini",
            temperature=Decimal("0.70"),
            max_tokens=600,
            price_per_improve=1,
            daily_limit_per_user=20,
            pack_price_credits=3,
            pack_size_improvements=10,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_config(
        self,
        *,
        enabled: bool,
        openai_model: str,
        temperature: float,
        max_tokens: int,
        price_per_improve: int,
        daily_limit_per_user: int,
        pack_price_credits: int,
        pack_size_improvements: int,
        system_prompt: str,
    ) -> AIBrainConfig:
        cfg = await self.get_config()
        cfg.enabled = bool(enabled)
        cfg.openai_model = (openai_model or cfg.openai_model).strip() or "gpt-4o-mini"
        cfg.temperature = Decimal(f"{max(0.0, min(2.0, float(temperature))):.2f}")
        cfg.max_tokens = max(64, min(4096, int(max_tokens)))
        cfg.price_per_improve = max(0, int(price_per_improve))
        cfg.daily_limit_per_user = max(0, int(daily_limit_per_user))
        cfg.pack_price_credits = max(0, int(pack_price_credits))
        cfg.pack_size_improvements = max(1, int(pack_size_improvements))
        cfg.system_prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
        cfg.updated_at = utcnow()
        await self.session.flush()
        return cfg

    async def get_daily_success_count(self, user_id: int) -> int:
        since = utcnow() - timedelta(days=1)
        result = await self.session.execute(
            select(func.count(AIBrainLog.id))
            .where(AIBrainLog.user_id == user_id)
            .where(AIBrainLog.action == "improve_prompt")
            .where(AIBrainLog.status == "success")
            .where(AIBrainLog.created_at >= since)
        )
        return int(result.scalar_one() or 0)

    async def _get_balance_row(self, user_id: int, *, create: bool) -> AIImprovementBalance | None:
        result = await self.session.execute(
            select(AIImprovementBalance).where(AIImprovementBalance.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row or not create:
            return row

        now = utcnow()
        row = AIImprovementBalance(
            user_id=user_id,
            remaining_improvements=0,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_remaining_improvements(self, user_id: int) -> int:
        row = await self._get_balance_row(user_id, create=False)
        if not row:
            return 0
        return max(0, int(row.remaining_improvements or 0))

    async def add_pack_improvements(self, user_id: int, amount: int) -> int:
        delta = max(0, int(amount))
        row = await self._get_balance_row(user_id, create=True)
        assert row is not None
        row.remaining_improvements = max(0, int(row.remaining_improvements or 0) + delta)
        row.updated_at = utcnow()
        await self.session.flush()
        return int(row.remaining_improvements)

    async def consume_for_improvement(self, user: User, *, price_per_improve: int, request_id: str) -> BrainChargeResult:
        row = await self._get_balance_row(user.id, create=True)
        assert row is not None

        remaining_before = max(0, int(row.remaining_improvements or 0))
        if remaining_before > 0:
            row.remaining_improvements = remaining_before - 1
            row.updated_at = utcnow()
            await self.session.flush()
            return BrainChargeResult(
                source="pack",
                spent_credits=0,
                remaining_improvements=int(row.remaining_improvements),
                balance_credits=int(user.balance_credits),
            )

        price = max(0, int(price_per_improve))
        if price > 0:
            if int(user.balance_credits or 0) < price:
                raise ValueError("insufficient_credits")
            credits_service = CreditsService(self.session)
            await credits_service.add_ledger(
                user,
                -price,
                "brain_improve_charge",
                meta={"request_id": request_id},
                idempotency_key=f"brain:improve:{request_id}",
            )
        row.updated_at = utcnow()
        await self.session.flush()
        return BrainChargeResult(
            source="credits",
            spent_credits=price,
            remaining_improvements=int(row.remaining_improvements or 0),
            balance_credits=int(user.balance_credits),
        )

    async def purchase_pack(
        self,
        user: User,
        *,
        pack_price_credits: int,
        pack_size_improvements: int,
        request_id: str,
    ) -> BrainChargeResult:
        price = max(0, int(pack_price_credits))
        size = max(1, int(pack_size_improvements))
        if price > 0 and int(user.balance_credits or 0) < price:
            raise ValueError("insufficient_credits")

        if price > 0:
            credits_service = CreditsService(self.session)
            await credits_service.add_ledger(
                user,
                -price,
                "brain_pack_purchase",
                meta={"request_id": request_id, "pack_size_improvements": size},
                idempotency_key=f"brain:pack:{request_id}",
            )

        remaining = await self.add_pack_improvements(user.id, size)
        return BrainChargeResult(
            source="credits",
            spent_credits=price,
            remaining_improvements=remaining,
            balance_credits=int(user.balance_credits),
        )

    async def log_improve(
        self,
        *,
        user_id: int,
        action: str,
        status: str,
        prompt_original: str,
        prompt_result: str | None,
        model: str,
        temperature: float,
        max_tokens: int,
        source: str = "none",
        spent_credits: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AIBrainLog(
                user_id=user_id,
                action=(action or "improve_prompt").strip(),
                status=(status or "error").strip(),
                source=(source or "none").strip(),
                spent_credits=max(0, int(spent_credits)),
                prompt_original=prompt_original,
                prompt_result=prompt_result,
                model=(model or "unknown").strip(),
                temperature=Decimal(f"{max(0.0, min(2.0, float(temperature))):.2f}"),
                max_tokens=max(1, int(max_tokens)),
                error_code=(error_code or "").strip() or None,
                error_message=(error_message or "").strip() or None,
                meta=meta or {},
                created_at=utcnow(),
            )
        )
        await self.session.flush()

    async def _call_openai_chat_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self.openai_api_key:
            raise BrainProviderError("openai_not_configured", "OpenAI API key is not configured")

        url = f"{self.openai_base_url}/chat/completions"
        payload = {
            "model": model,
            "temperature": max(0.0, min(2.0, float(temperature))),
            "max_tokens": max(1, int(max_tokens)),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise BrainProviderError("openai_request_failed", str(exc)) from exc

        data: dict[str, Any]
        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code >= 400:
            details = ""
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    details = str(err.get("message") or err.get("code") or "").strip()
                elif err:
                    details = str(err).strip()
            raise BrainProviderError("openai_bad_response", details or f"HTTP {response.status_code}")

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise BrainProviderError("openai_empty_response", "No choices returned")

        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text_part = item.get("text")
                    if isinstance(text_part, str):
                        parts.append(text_part)
            content_text = "\n".join(part.strip() for part in parts if part.strip()).strip()
        else:
            content_text = str(content or "").strip()

        if not content_text:
            raise BrainProviderError("openai_empty_response", "Empty completion text")
        return content_text

    async def improvePrompt(self, prompt: str, config: AIBrainConfig | None = None) -> str:
        cfg = config or await self.get_config()
        return await self._call_openai_chat_completion(
            model=(cfg.openai_model or "gpt-4o-mini").strip(),
            system_prompt=(cfg.system_prompt or DEFAULT_SYSTEM_PROMPT).strip() or DEFAULT_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=float(cfg.temperature or 0.7),
            max_tokens=int(cfg.max_tokens or 600),
        )

    async def improve_prompt(self, prompt: str, config: AIBrainConfig | None = None) -> str:
        return await self.improvePrompt(prompt, config)

    async def remixPrompt(self, *_args, **_kwargs) -> str:
        raise NotImplementedError("remixPrompt is not implemented yet")

    async def scorePrompt(self, *_args, **_kwargs) -> str:
        raise NotImplementedError("scorePrompt is not implemented yet")

    async def autoEnhance(self, *_args, **_kwargs) -> str:
        raise NotImplementedError("autoEnhance is not implemented yet")

