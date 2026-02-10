from __future__ import annotations

from typing import Any

import httpx


class CryptoCloudError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CryptoCloudClient:
    def __init__(self, api_key: str, shop_id: str, base_url: str = "https://api.cryptocloud.plus") -> None:
        self.api_key = api_key.strip()
        self.shop_id = shop_id.strip()
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }

    async def create_invoice(
        self,
        amount: float,
        currency: str,
        order_id: str,
        locale: str = "en",
    ) -> dict[str, Any]:
        payload = {
            "shop_id": self.shop_id,
            "amount": amount,
            "currency": currency.upper(),
            "order_id": order_id,
        }
        params = {"locale": locale}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{self.base_url}/v2/invoice/create",
                params=params,
                headers=self._headers(),
                json=payload,
            )
        if resp.status_code >= 400:
            raise CryptoCloudError(f"create_invoice_failed:{resp.text}", resp.status_code)
        data = resp.json()
        if str(data.get("status", "")).lower() != "success":
            raise CryptoCloudError(f"create_invoice_rejected:{data}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise CryptoCloudError("create_invoice_invalid_response")
        return result

    async def merchant_invoices(self, uuids: list[str]) -> list[dict[str, Any]]:
        payload = {"uuids": uuids}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{self.base_url}/v2/invoice/merchant/info",
                headers=self._headers(),
                json=payload,
            )
        if resp.status_code >= 400:
            raise CryptoCloudError(f"invoice_info_failed:{resp.text}", resp.status_code)
        data = resp.json()
        if str(data.get("status", "")).lower() != "success":
            raise CryptoCloudError(f"invoice_info_rejected:{data}")
        result = data.get("result")
        if not isinstance(result, list):
            raise CryptoCloudError("invoice_info_invalid_response")
        return [item for item in result if isinstance(item, dict)]

    async def invoice_status(self, uuid: str) -> dict[str, Any] | None:
        rows = await self.merchant_invoices([uuid])
        for row in rows:
            if str(row.get("uuid", "")).strip() == uuid:
                return row
        return rows[0] if rows else None
