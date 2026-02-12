from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.states import GenerateFlow
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.services.brain import AIBrainService, BrainProviderError
from app.services.credits import CreditsService
from app.services.rate_limit import RateLimiter
from app.utils.text import clamp_text, escape_html


router = Router()
_settings = get_settings()
_improve_rate_limiter = RateLimiter(_settings.per_user_generate_cooldown_seconds)


def _brain_apply_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "brain_bot_use"), callback_data="gen:improve:use")],
            [InlineKeyboardButton(text=t(lang, "brain_bot_restore"), callback_data="gen:improve:restore")],
            [InlineKeyboardButton(text=t(lang, "confirm_cancel"), callback_data="gen:improve:cancel")],
        ]
    )


@router.callback_query(GenerateFlow.confirming, F.data == "gen:improve")
async def improve_prompt_in_bot(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    lang = get_lang(callback.from_user)
    if not _improve_rate_limiter.allow(callback.from_user.id):
        await callback.answer(t(lang, "brain_rate_limited"), show_alert=True)
        return

    data = await state.get_data()
    prompt_raw = str(data.get("prompt") or "")
    prompt = prompt_raw.strip()
    if not prompt:
        await callback.answer(t(lang, "prompt_empty_bot"), show_alert=True)
        return
    if _settings.max_prompt_length > 0 and len(prompt_raw) > _settings.max_prompt_length:
        await callback.answer(tf(lang, "prompt_too_long", max=_settings.max_prompt_length), show_alert=True)
        return

    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t(lang, "history_user_not_found"), show_alert=True)
        return

    brain = AIBrainService(
        session,
        openai_api_key=_settings.openai_api_key,
        openai_base_url=_settings.openai_base_url,
    )
    action_id = uuid.uuid4().hex
    cfg = await brain.get_config()
    if not cfg.enabled or not _settings.openai_api_key.strip():
        await callback.answer(t(lang, "brain_unavailable"), show_alert=True)
        return

    daily_used = await brain.get_daily_success_count(user.id)
    daily_limit = max(0, int(cfg.daily_limit_per_user or 0))
    if daily_limit > 0 and daily_used >= daily_limit:
        await callback.answer(t(lang, "brain_daily_limit_reached"), show_alert=True)
        return

    pack_remaining_before = await brain.get_remaining_improvements(user.id)
    price_per_improve = max(0, int(cfg.price_per_improve or 0))
    if pack_remaining_before <= 0 and price_per_improve > 0 and int(user.balance_credits or 0) < price_per_improve:
        await callback.answer(t(lang, "brain_not_enough_credits"), show_alert=True)
        return

    try:
        improved_prompt = await brain.improvePrompt(prompt, cfg)
    except BrainProviderError as exc:
        await brain.log_improve(
            user_id=user.id,
            action="improve_prompt",
            status="error",
            source="none",
            spent_credits=0,
            prompt_original=prompt,
            prompt_result=None,
            model=(cfg.openai_model or "gpt-4o-mini").strip(),
            temperature=float(cfg.temperature or 0.7),
            max_tokens=max(1, int(cfg.max_tokens or 600)),
            error_code=exc.code,
            error_message=exc.message,
            meta={"request_id": action_id, "from": "telegram_bot"},
        )
        await session.commit()
        await callback.answer(t(lang, "brain_failed"), show_alert=True)
        return

    improved_prompt = clamp_text(improved_prompt or "", _settings.max_prompt_length).strip()
    if not improved_prompt:
        await callback.answer(t(lang, "brain_failed"), show_alert=True)
        return

    try:
        charge = await brain.consume_for_improvement(
            user,
            price_per_improve=price_per_improve,
            request_id=action_id,
        )
    except ValueError:
        await callback.answer(t(lang, "brain_not_enough_credits"), show_alert=True)
        return

    await brain.log_improve(
        user_id=user.id,
        action="improve_prompt",
        status="success",
        source=charge.source,
        spent_credits=charge.spent_credits,
        prompt_original=prompt,
        prompt_result=improved_prompt,
        model=(cfg.openai_model or "gpt-4o-mini").strip(),
        temperature=float(cfg.temperature or 0.7),
        max_tokens=max(1, int(cfg.max_tokens or 600)),
        meta={
            "request_id": action_id,
            "from": "telegram_bot",
            "daily_used_after": daily_used + 1,
            "daily_limit": daily_limit,
        },
    )
    await session.commit()

    await state.update_data(
        brain_original_prompt=prompt_raw,
        brain_improved_prompt=improved_prompt,
    )

    charge_line = (
        t(lang, "brain_pack_used")
        if charge.source == "pack"
        else tf(lang, "brain_credits_used", credits=max(0, int(charge.spent_credits)))
    )
    preview = escape_html(clamp_text(improved_prompt, 1200))
    text = (
        f"{t(lang, 'brain_improve_success')}\n"
        f"{charge_line}\n\n"
        f"{t(lang, 'brain_bot_result_hint')}\n"
        f"{preview}"
    )
    try:
        await callback.message.edit_text(text, reply_markup=_brain_apply_menu(lang))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=_brain_apply_menu(lang))
    await callback.answer()


@router.callback_query(GenerateFlow.confirming, F.data == "gen:improve:use")
async def improve_use(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    from app.bot.handlers.generate import _show_preview

    data = await state.get_data()
    improved = str(data.get("brain_improved_prompt") or "").strip()
    lang = get_lang(callback.from_user)
    if not improved:
        await callback.answer(t(lang, "brain_failed"), show_alert=True)
        return
    await state.update_data(prompt=improved)
    await _show_preview(callback, state, session)
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.confirming, F.data == "gen:improve:restore")
async def improve_restore(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    from app.bot.handlers.generate import _show_preview

    data = await state.get_data()
    original = str(data.get("brain_original_prompt") or "").strip()
    lang = get_lang(callback.from_user)
    if not original:
        await callback.answer(t(lang, "brain_failed"), show_alert=True)
        return
    await state.update_data(prompt=original)
    await _show_preview(callback, state, session)
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.confirming, F.data == "gen:improve:cancel")
async def improve_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    from app.bot.handlers.generate import _show_preview

    await _show_preview(callback, state, session)
    await safe_cleanup_callback(callback)
