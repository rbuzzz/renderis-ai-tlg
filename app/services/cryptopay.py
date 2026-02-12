from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx


class CryptoPayError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CryptoPayClient:
    def __init__(self, api_token: str, base_url: str = "https://pay.crypt.bot", timeout: float = 20.0) -> None:
        self.api_token = api_token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Crypto-Pay-API-Token": self.api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api_url(self, method: str) -> str:
        return f"{self.base_url}/api/{method}"

    @staticmethod
    def _as_amount(value: Decimal | float | int | str) -> str:
        dec = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if dec <= Decimal("0"):
            dec = Decimal("0.01")
        return format(dec, "f")

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self._api_url(method), headers=self._headers(), json=payload or {})
        if resp.status_code >= 400:
            raise CryptoPayError(f"{method}_http_failed:{resp.text}", resp.status_code)
        data = resp.json()
        if not bool(data.get("ok")):
            raise CryptoPayError(f"{method}_rejected:{data}")
        return data.get("result")

    async def create_invoice(
        self,
        *,
        amount: Decimal | float | int | str,
        currency_type: str = "fiat",
        asset: str | None = None,
        fiat: str = "USD",
        accepted_assets: str | None = None,
        swap_to: str | None = None,
        description: str | None = None,
        payload: str | None = None,
        allow_comments: bool | None = None,
        allow_anonymous: bool | None = None,
        expires_in: int | None = None,
        paid_btn_name: str | None = None,
        paid_btn_url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "currency_type": str(currency_type or "fiat").strip().lower(),
            "amount": self._as_amount(amount),
        }
        if body["currency_type"] == "crypto":
            body["asset"] = (asset or "").upper()
        else:
            body["fiat"] = (fiat or "USD").upper()
            if accepted_assets:
                body["accepted_assets"] = accepted_assets
        if swap_to:
            body["swap_to"] = swap_to.upper()
        if description:
            body["description"] = description[:1024]
        if payload:
            body["payload"] = payload[:4096]
        if allow_comments is not None:
            body["allow_comments"] = bool(allow_comments)
        if allow_anonymous is not None:
            body["allow_anonymous"] = bool(allow_anonymous)
        if expires_in is not None and int(expires_in) > 0:
            body["expires_in"] = int(expires_in)
        if paid_btn_name:
            body["paid_btn_name"] = paid_btn_name
        if paid_btn_url:
            body["paid_btn_url"] = paid_btn_url

        result = await self._call("createInvoice", body)
        if not isinstance(result, dict):
            raise CryptoPayError("createInvoice_invalid_response")
        if not result.get("invoice_id"):
            raise CryptoPayError("createInvoice_missing_invoice_id")
        return result

    async def get_invoices(
        self,
        *,
        invoice_ids: list[str] | list[int] | None = None,
        status: str | None = None,
        count: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {}
        if invoice_ids:
            body["invoice_ids"] = ",".join(str(x).strip() for x in invoice_ids if str(x).strip())
        if status:
            body["status"] = status
        if count is not None:
            body["count"] = int(count)
        if offset is not None:
            body["offset"] = int(offset)
        result = await self._call("getInvoices", body)
        if not isinstance(result, list):
            raise CryptoPayError("getInvoices_invalid_response")
        return [row for row in result if isinstance(row, dict)]

    async def get_invoice(self, invoice_id: str | int) -> dict[str, Any] | None:
        value = str(invoice_id).strip()
        if not value:
            return None
        rows = await self.get_invoices(invoice_ids=[value], count=1)
        for row in rows:
            if str(row.get("invoice_id") or "").strip() == value:
                return row
        return rows[0] if rows else None

    @staticmethod
    def compute_webhook_signature(api_token: str, raw_body: bytes) -> str:
        secret = hashlib.sha256(api_token.encode("utf-8")).digest()
        return hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

    @classmethod
    def verify_webhook_signature(cls, *, api_token: str, raw_body: bytes, signature: str) -> bool:
        token = (api_token or "").strip()
        header_signature = (signature or "").strip().lower()
        if not token or not header_signature:
            return False
        expected = cls.compute_webhook_signature(token, raw_body).lower()
        return hmac.compare_digest(expected, header_signature)
