from __future__ import annotations

from contextvars import ContextVar, Token

from aiogram.types import User as TgUser

from app.i18n import normalize_lang, t, tf


_CURRENT_LANG: ContextVar[str | None] = ContextVar("bot_current_lang", default=None)


def set_current_lang(lang: str | None) -> Token:
    if lang:
        return _CURRENT_LANG.set(normalize_lang(lang))
    return _CURRENT_LANG.set(None)


def reset_current_lang(token: Token) -> None:
    _CURRENT_LANG.reset(token)


def get_lang(user: TgUser | None) -> str:
    current = _CURRENT_LANG.get()
    if current:
        return current
    if not user:
        return "en"
    return normalize_lang(user.language_code)


__all__ = ["get_lang", "set_current_lang", "reset_current_lang", "t", "tf"]
