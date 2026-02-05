from __future__ import annotations

import html
from typing import Optional


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def clamp_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + '...'


def format_username(username: Optional[str], user_id: int) -> str:
    if username:
        return f'@{username}'
    return str(user_id)
