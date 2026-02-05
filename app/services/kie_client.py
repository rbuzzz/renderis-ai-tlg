from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings
from app.utils.logging import get_logger


logger = get_logger('kie')


class KieError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KieClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = 'https://api.kie.ai/api/v1'
        self.api_key = settings.kie_api_key
        self._client = httpx.AsyncClient(timeout=60)

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> Dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    async def create_task(self, model_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f'{self.base_url}/jobs/createTask'
        body = {
            'model': model_id,
            'input': payload,
        }
        resp = await self._client.post(url, headers=self._headers(), json=body)
        if resp.status_code >= 400:
            raise KieError(f'Kie createTask error {resp.status_code}: {resp.text}', resp.status_code)
        data = resp.json()
        return data

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        url = f'{self.base_url}/jobs/recordInfo'
        resp = await self._client.get(url, headers=self._headers(), params={'taskId': task_id})
        if resp.status_code >= 400:
            raise KieError(f'Kie recordInfo error {resp.status_code}: {resp.text}', resp.status_code)
        return resp.json()

    def parse_result_urls(self, record: Dict[str, Any]) -> List[str]:
        data = record.get('data') or {}
        result_json = data.get('resultJson') or '{}'
        try:
            import json

            parsed = json.loads(result_json)
            urls = parsed.get('resultUrls') or []
            return [u for u in urls if isinstance(u, str)]
        except Exception as exc:
            logger.warning('failed_to_parse_result', error=str(exc))
            return []

    def get_status(self, record: Dict[str, Any]) -> str:
        data = record.get('data') or {}
        # Kie returns `state` (waiting/success/fail). Some responses may include `status`.
        return str(data.get('state') or data.get('status') or '')

    def get_fail_info(self, record: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        data = record.get('data') or {}
        return data.get('failCode'), data.get('failMsg')
