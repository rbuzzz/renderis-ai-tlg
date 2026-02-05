from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict


@dataclass
class CooldownState:
    last_action: float = 0.0


class RateLimiter:
    def __init__(self, cooldown_seconds: int) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._state: Dict[int, CooldownState] = {}

    def allow(self, user_id: int) -> bool:
        now = time.time()
        state = self._state.get(user_id) or CooldownState()
        if now - state.last_action < self._cooldown_seconds:
            self._state[user_id] = state
            return False
        state.last_action = now
        self._state[user_id] = state
        return True
