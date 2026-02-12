from __future__ import annotations

import base64
import hmac
import json
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
        self.callback_url = settings.kie_callback_url.strip()
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
        if self.callback_url:
            body['callBackUrl'] = self.callback_url
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

    @staticmethod
    def extract_task_id(record: Dict[str, Any]) -> str:
        data = record.get('data') if isinstance(record.get('data'), dict) else {}
        candidates = [
            record.get('taskId'),
            record.get('task_id'),
            data.get('taskId'),
            data.get('task_id'),
        ]
        for candidate in candidates:
            value = str(candidate or '').strip()
            if value:
                return value
        return ''

    @staticmethod
    def compute_webhook_signature(task_id: str, timestamp_seconds: str, webhook_hmac_key: str) -> str:
        message = f'{task_id}.{timestamp_seconds}'
        digest = hmac.new(
            webhook_hmac_key.encode('utf-8'),
            message.encode('utf-8'),
            'sha256',
        ).digest()
        return base64.b64encode(digest).decode('utf-8')

    @classmethod
    def verify_webhook_signature(
        cls,
        *,
        task_id: str,
        timestamp_seconds: str,
        received_signature: str,
        webhook_hmac_key: str,
    ) -> bool:
        expected = cls.compute_webhook_signature(task_id, timestamp_seconds, webhook_hmac_key)
        received = (received_signature or '').strip()
        return hmac.compare_digest(expected, received)

    def parse_result_urls(self, record: Dict[str, Any]) -> List[str]:
        data = record.get('data') if isinstance(record.get('data'), dict) else {}
        urls: List[str] = []

        def extend_from(value: Any) -> None:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    urls.append(cleaned)
                return
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        cleaned = item.strip()
                        if cleaned:
                            urls.append(cleaned)

        extend_from(data.get('resultUrls'))
        extend_from(data.get('result_urls'))
        extend_from(record.get('resultUrls'))
        extend_from(record.get('result_urls'))

        result_json = data.get('resultJson') or data.get('result_json') or {}
        parsed: Dict[str, Any] = {}
        try:
            if isinstance(result_json, str):
                parsed = json.loads(result_json) if result_json else {}
            elif isinstance(result_json, dict):
                parsed = result_json
        except Exception as exc:
            logger.warning('failed_to_parse_result', error=str(exc))
        if parsed:
            extend_from(parsed.get('resultUrls'))
            extend_from(parsed.get('result_urls'))
            extend_from(parsed.get('urls'))
            output = parsed.get('output')
            if isinstance(output, dict):
                extend_from(output.get('resultUrls'))
                extend_from(output.get('result_urls'))
                extend_from(output.get('urls'))

        # Preserve order while removing duplicates.
        return list(dict.fromkeys(urls))

    def get_status(self, record: Dict[str, Any]) -> str:
        data = record.get('data') or {}
        callback_type = str(
            data.get('callbackType')
            or data.get('callback_type')
            or record.get('callbackType')
            or record.get('callback_type')
            or ''
        ).strip().lower()
        if callback_type in {'task_completed', 'task_success', 'completed', 'success', 'succeeded', 'done'}:
            return 'success'
        if callback_type in {'task_failed', 'task_fail', 'task_error', 'failed', 'fail', 'error'}:
            return 'fail'

        # Kie recordInfo returns `state` (waiting/success/fail). Some responses may include `status`.
        return str(
            data.get('state')
            or data.get('status')
            or record.get('state')
            or record.get('status')
            or ''
        )

    def get_fail_info(self, record: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        data = record.get('data') or {}
        fail_code = data.get('failCode') or data.get('fail_code')
        fail_msg = data.get('failMsg') or data.get('fail_msg') or data.get('error') or record.get('msg')
        if not fail_code:
            code = record.get('code')
            if code not in (None, '', 200, '200'):
                fail_code = str(code)
        return fail_code, fail_msg
