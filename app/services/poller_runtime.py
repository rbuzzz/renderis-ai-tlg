from __future__ import annotations

from typing import Optional

from app.services.poller import PollManager


_poller: Optional[PollManager] = None


def set_poller(poller: PollManager) -> None:
    global _poller
    _poller = poller


def get_poller() -> Optional[PollManager]:
    return _poller
