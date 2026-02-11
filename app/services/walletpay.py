from __future__ import annotations

import base64
import hashlib
import hmac
from decimal import Decimal
from typing import Any

import httpx


class WalletPayError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WalletPayClient:
    def __init__(self, api_key: str, base_url: str = "https://pay.wallet.tg", timeout: float = 15.0) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Wpay-Store-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _as_amount(amount: Decimal) -> str:
        normalized = amount.quantize(Decimal("0.01"))
        if normalized <= 0:
            normalized = Decimal("0.01")
        return format(normalized, "f")

    async def create_order(
        self,
        *,
        amount: Decimal,
        currency_code: str,
        external_id: str,
        description: str,
        timeout_seconds: int,
        customer_telegram_user_id: int,
        return_url: str | None = None,
        fail_return_url: str | None = None,
        custom_data: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "amount": {
                "amount": self._as_amount(amount),
                "currencyCode": currency_code.upper(),
            },
            "externalId": external_id,
            "timeoutSeconds": int(timeout_seconds),
            "description": description[:100],
            "customerTelegramUserId": int(customer_telegram_user_id),
        }
        if return_url:
            payload["returnUrl"] = return_url
        if fail_return_url:
            payload["failReturnUrl"] = fail_return_url
        if custom_data:
            payload["customData"] = custom_data[:255]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/wpay/store-api/v1/order", headers=self._headers(), json=payload)

        if resp.status_code >= 400:
            raise WalletPayError(f"create_order_failed:{resp.text}", resp.status_code)

        data = resp.json()
        status = str(data.get("status") or "").upper()
        if status not in {"SUCCESS", "ALREADY"}:
            raise WalletPayError(f"create_order_rejected:{data}")

        preview = data.get("data") or {}
        if not preview.get("id") or not preview.get("directPayLink"):
            raise WalletPayError(f"create_order_invalid_response:{data}")
        return preview

    async def get_order_preview(self, order_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/wpay/store-api/v1/order/preview",
                headers=self._headers(),
                params={"id": str(order_id)},
            )

        if resp.status_code >= 400:
            raise WalletPayError(f"order_preview_failed:{resp.text}", resp.status_code)

        data = resp.json()
        status = str(data.get("status") or "").upper()
        if status != "SUCCESS":
            raise WalletPayError(f"order_preview_rejected:{data}")

        preview = data.get("data") or {}
        if not preview:
            raise WalletPayError("order_preview_empty")
        return preview

    @staticmethod
    def compute_webhook_signature(
        *,
        api_key: str,
        http_method: str,
        uri_path: str,
        timestamp: str,
        body: bytes,
    ) -> str:
        base64_body = base64.b64encode(body).decode("ascii")
        string_to_sign = f"{http_method.upper()}.{uri_path}.{timestamp}.{base64_body}"
        digest = hmac.new(api_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("ascii")

    @classmethod
    def verify_webhook_signature(
        cls,
        *,
        api_key: str,
        http_method: str,
        uri_path: str,
        timestamp: str,
        body: bytes,
        signature: str,
    ) -> bool:
        if not api_key or not timestamp or not signature:
            return False

        path_variants = [uri_path]
        if uri_path.endswith("/"):
            path_variants.append(uri_path.rstrip("/"))
        else:
            path_variants.append(uri_path + "/")

        for candidate in path_variants:
            expected = cls.compute_webhook_signature(
                api_key=api_key,
                http_method=http_method,
                uri_path=candidate,
                timestamp=timestamp,
                body=body,
            )
            if hmac.compare_digest(expected, signature):
                return True
        return False
