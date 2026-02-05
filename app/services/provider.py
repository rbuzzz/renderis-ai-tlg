from __future__ import annotations

from typing import Any, Dict, List, Protocol


class ProviderClient(Protocol):
    async def create_task(self, model_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        ...

    def parse_result_urls(self, record: Dict[str, Any]) -> List[str]:
        ...
