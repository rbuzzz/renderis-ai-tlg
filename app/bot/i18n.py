from __future__ import annotations

from aiogram.types import User as TgUser

from app.i18n import normalize_lang, t, tf


def get_lang(user: TgUser | None) -> str:
    if not user:
        return "ru"
    return normalize_lang(user.language_code)


__all__ = ["get_lang", "t", "tf"]
