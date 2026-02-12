from __future__ import annotations

from typing import Any, Dict, List, Tuple

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.states import GenerateFlow
from app.bot.utils import safe_cleanup_callback
from app.db.models import Generation, GenerationTask
from app.modelspecs.base import ModelSpec
from app.modelspecs.registry import get_model, list_models
from app.services.credits import CreditsService
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.poller_runtime import get_poller
from app.services.pricing import PricingService
from app.services.rate_limit import RateLimiter
from app.config import get_settings


router = Router()
_settings = get_settings()
_action_rate_limiter = RateLimiter(_settings.per_user_generate_cooldown_seconds)


def _action_confirm_menu(mode: str, generation_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "result_action_confirm"), callback_data=f"gen:action:confirm:{mode}:{generation_id}")],
            [InlineKeyboardButton(text=t(lang, "result_action_cancel"), callback_data="gen:action:cancel")],
        ]
    )


def _edit_model_menu(generation_id: int, lang: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for model in list_models():
        if not model.model_type == "image":
            continue
        if not (model.supports_reference_images or model.requires_reference_images):
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=model.display_name,
                    callback_data=f"gen:result:editai:model:{model.key}:{generation_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=t(lang, "result_action_cancel"), callback_data="gen:action:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_user(session: AsyncSession, telegram_id: int):
    credits = CreditsService(session)
    return await credits.get_user(telegram_id)


async def _first_result_url(session: AsyncSession, generation_id: int) -> str | None:
    result = await session.execute(
        select(GenerationTask).where(
            GenerationTask.generation_id == generation_id,
            GenerationTask.state == "success",
        )
    )
    tasks = list(result.scalars().all())
    for task in tasks:
        for url in task.result_urls or []:
            if isinstance(url, str) and url.strip():
                return url.strip()
    return None


async def _queue_position(session: AsyncSession) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count(GenerationTask.id)).where(GenerationTask.state.in_(["queued", "pending", "running"]))
    )
    return int(result.scalar_one() or 0)


def _apply_upscale_options(model: ModelSpec, options: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(options)
    resolution = model.option_by_key("resolution")
    if not resolution:
        return updated
    allowed = {v.value for v in resolution.values}
    if "4K" in allowed:
        updated["resolution"] = "4K"
    return updated


def _resolve_action_model(mode: str, generation: Generation) -> ModelSpec | None:
    if mode == "upscale":
        return get_model("nano_banana_pro")
    return get_model(generation.model)


def _source_aspect_ratio(generation: Generation) -> str | None:
    if not isinstance(generation.options, dict):
        return None
    ratio = generation.options.get("aspect_ratio") or generation.options.get("image_size")
    if not isinstance(ratio, str):
        return None
    value = ratio.strip()
    return value or None


def _prepare_action_payload(
    mode: str,
    model: ModelSpec,
    generation: Generation,
    result_url: str | None,
) -> Tuple[str, Dict[str, Any], int, List[str]]:
    raw_options: Dict[str, Any] = dict(generation.options or {})
    raw_options.pop("reference_urls", None)
    raw_options.pop("reference_files", None)
    options = model.validate_options(raw_options)

    prompt = generation.prompt
    outputs = 1
    ref_urls: List[str] = []

    existing_refs = generation.options.get("reference_urls") if isinstance(generation.options, dict) else None
    if isinstance(existing_refs, list):
        ref_urls = [u for u in existing_refs if isinstance(u, str) and u.strip()]

    if mode in {"variation", "remix", "upscale"} and result_url and (model.supports_reference_images or model.requires_reference_images):
        if result_url not in ref_urls:
            ref_urls.append(result_url)
        if model.option_by_key("reference_images"):
            options["reference_images"] = "has"

    if mode == "variation":
        prompt = f"{generation.prompt}\n\nSlightly change the composition and details while preserving the original style and subject."
    elif mode == "remix":
        prompt = f"{generation.prompt}\n\nRemix this concept with a fresh creative direction while keeping the core subject."
    elif mode == "upscale":
        ratio = _source_aspect_ratio(generation)
        aspect_ratio_option = model.option_by_key("aspect_ratio")
        if ratio and aspect_ratio_option:
            allowed = {v.value for v in aspect_ratio_option.values}
            if ratio in allowed:
                options["aspect_ratio"] = ratio
        if model.option_by_key("reference_images"):
            options["reference_images"] = "has"
        options = _apply_upscale_options(model, options)

    return prompt, options, outputs, ref_urls


async def _ask_confirm_action(callback: CallbackQuery, session: AsyncSession, mode: str, generation_id: int) -> None:
    lang = get_lang(callback.from_user)
    generation = await session.get(Generation, generation_id)
    if not generation:
        await callback.answer(t(lang, "history_not_found"), show_alert=True)
        return

    user = await _get_user(session, callback.from_user.id)
    if not user or generation.user_id != user.id:
        await callback.answer(t(lang, "history_no_access"), show_alert=True)
        return

    model = _resolve_action_model(mode, generation)
    if not model:
        await callback.answer(t(lang, "model_not_found"), show_alert=True)
        return

    preview_url = await _first_result_url(session, generation.id)
    if mode == "upscale" and not preview_url:
        await callback.answer(t(lang, "history_not_ready"), show_alert=True)
        return
    prompt, options, outputs, _ref_urls = _prepare_action_payload(mode, model, generation, preview_url)
    pricing = PricingService(session)
    breakdown = await pricing.resolve_cost(model, options, outputs, int(user.referral_discount_pct or 0))

    mode_label = t(lang, f"result_mode_{mode}")
    text = (
        f"{tf(lang, 'result_action_confirm_text', action=mode_label)}\n"
        f"{tf(lang, 'preview_model', model=model.display_name)}\n"
        f"{tf(lang, 'preview_cost_per', cost=breakdown.per_output)}\n"
        f"{tf(lang, 'preview_total', total=breakdown.total)}\n"
    )
    if mode in {"variation", "remix"} and prompt:
        text += f"\n{t(lang, 'result_action_prompt_hint')}"
    await callback.message.answer(text, reply_markup=_action_confirm_menu(mode, generation_id, lang))
    await callback.answer()


@router.callback_query(F.data.startswith("gen:result:variation:"))
async def result_variation(callback: CallbackQuery, session: AsyncSession) -> None:
    generation_id = int(callback.data.split(":")[3])
    await _ask_confirm_action(callback, session, "variation", generation_id)


@router.callback_query(F.data.startswith("gen:result:remix:"))
async def result_remix(callback: CallbackQuery, session: AsyncSession) -> None:
    generation_id = int(callback.data.split(":")[3])
    await _ask_confirm_action(callback, session, "remix", generation_id)


@router.callback_query(F.data.startswith("gen:result:upscale:"))
async def result_upscale(callback: CallbackQuery, session: AsyncSession) -> None:
    generation_id = int(callback.data.split(":")[3])
    await _ask_confirm_action(callback, session, "upscale", generation_id)


@router.callback_query(F.data.startswith("gen:action:confirm:"))
async def result_action_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    _prefix, _action, _confirm, mode, gen_id_raw = parts
    if mode not in {"variation", "remix", "upscale"}:
        await callback.answer()
        return
    generation_id = int(gen_id_raw)
    lang = get_lang(callback.from_user)

    if mode in {"variation", "remix", "upscale"} and not _action_rate_limiter.allow(callback.from_user.id):
        await callback.answer(t(lang, "error_too_many"), show_alert=True)
        return

    generation = await session.get(Generation, generation_id)
    if not generation:
        await callback.answer(t(lang, "history_not_found"), show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or generation.user_id != user.id:
        await callback.answer(t(lang, "history_no_access"), show_alert=True)
        return

    model = _resolve_action_model(mode, generation)
    if not model:
        await callback.answer(t(lang, "model_not_found"), show_alert=True)
        return

    preview_url = await _first_result_url(session, generation.id)
    if mode == "upscale" and not preview_url:
        await callback.answer(t(lang, "history_not_ready"), show_alert=True)
        return
    prompt, options, outputs, ref_urls = _prepare_action_payload(mode, model, generation, preview_url)

    kie = KieClient()
    gen_service = GenerationService(session, kie, callback.message.bot)
    try:
        new_gen = await gen_service.create_generation(
            user,
            model,
            prompt,
            options,
            outputs,
            ref_urls,
            None,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        code = str(exc)
        mapping = {
            "banned": "error_banned",
            "outputs": "error_outputs",
            "too_many": "error_too_many",
            "daily_cap": "error_daily_cap",
            "no_credits": "error_no_credits",
            "refs_required": "error_refs_required",
        }
        await callback.message.answer(t(lang, mapping.get(code, "error_generic")))
        await callback.answer()
        return
    except KieError as exc:
        await session.commit()
        if exc.status_code == 429:
            queue_pos = await _queue_position(session)
            await callback.message.answer(tf(lang, "queued", pos=queue_pos))
        elif exc.status_code in (401, 402):
            await callback.message.answer(t(lang, "api_auth_error"))
        else:
            await callback.message.answer(t(lang, "create_failed"))
        await callback.answer()
        return
    finally:
        await kie.close()

    result = await session.execute(select(GenerationTask.id).where(GenerationTask.generation_id == new_gen.id))
    task_ids = [row[0] for row in result.all()]
    poller = get_poller()
    if poller:
        for task_id in task_ids:
            poller.schedule(task_id)

    await callback.message.answer(t(lang, "task_started"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == "gen:action:cancel")
async def result_action_cancel(callback: CallbackQuery) -> None:
    await callback.answer(t(get_lang(callback.from_user), "repeat_cancelled"))
    await safe_cleanup_callback(callback)


@router.callback_query(lambda c: bool(c.data and c.data.startswith("gen:result:editai:") and ":model:" not in c.data))
async def result_edit_ai(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    lang = get_lang(callback.from_user)
    if len(parts) != 4:
        await callback.answer()
        return
    generation_id = int(parts[3])
    generation = await session.get(Generation, generation_id)
    if not generation:
        await callback.answer(t(lang, "history_not_found"), show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or generation.user_id != user.id:
        await callback.answer(t(lang, "history_no_access"), show_alert=True)
        return

    models = [m for m in list_models() if m.model_type == "image" and (m.supports_reference_images or m.requires_reference_images)]
    if not models:
        await callback.answer(t(lang, "result_edit_with_no_models"), show_alert=True)
        return

    await callback.message.answer(
        t(lang, "result_edit_with_title"),
        reply_markup=_edit_model_menu(generation_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gen:result:editai:model:"))
async def result_edit_ai_pick_model(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    lang = get_lang(callback.from_user)
    if len(parts) != 6:
        await callback.answer()
        return

    model_key = parts[4]
    generation_id = int(parts[5])
    model = get_model(model_key)
    if not model or not (model.supports_reference_images or model.requires_reference_images):
        await callback.answer(t(lang, "model_not_found"), show_alert=True)
        return

    generation = await session.get(Generation, generation_id)
    if not generation:
        await callback.answer(t(lang, "history_not_found"), show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or generation.user_id != user.id:
        await callback.answer(t(lang, "history_no_access"), show_alert=True)
        return

    result_url = await _first_result_url(session, generation.id)
    if not result_url:
        await callback.answer(t(lang, "history_not_ready"), show_alert=True)
        return

    options: Dict[str, Any] = {}
    for opt in model.options:
        options.setdefault(opt.key, opt.default)
    if model.option_by_key("reference_images"):
        options["reference_images"] = "has"
    options = model.validate_options(options)

    await state.clear()
    await state.update_data(
        model_key=model.key,
        options=options,
        outputs=1,
        ref_urls=[result_url],
        ref_files=[],
        ref_token=None,
        ref_required=False,
        ref_help_msg_id=None,
        prompt=generation.prompt,
    )
    await state.set_state(GenerateFlow.entering_prompt)

    await callback.message.answer(
        f"{t(lang, 'result_reference_added')}\n\n"
        f"{tf(lang, 'prompt_enter', model=model.display_name)}"
    )
    await callback.answer()
    await safe_cleanup_callback(callback)
