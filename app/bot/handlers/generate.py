from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.i18n import get_lang, t, tf
from app.bot.keyboards.main import (
    confirm_menu,
    generate_category_menu,
    model_menu,
    options_panel,
    repeat_confirm_menu,
    ref_mode_menu,
)
from app.bot.states import GenerateFlow
from app.bot.utils import safe_cleanup_callback
from app.config import get_settings
from app.db.models import Generation, GenerationTask
from app.db.session import create_sessionmaker
from app.modelspecs.registry import get_model, list_models
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.poller_runtime import get_poller
from app.services.progress import ProgressService
from app.services.pricing import PricingService
from app.services.rate_limit import RateLimiter
from app.utils.text import escape_html, clamp_text
from app.utils.time import utcnow


router = Router()
rate_limiter = RateLimiter(get_settings().per_user_generate_cooldown_seconds)


def _refs_menu(allow_skip: bool, lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t(lang, "ref_done_btn"), callback_data='gen:refs:done')]]
    if allow_skip:
        rows.append([InlineKeyboardButton(text=t(lang, "ref_skip_btn"), callback_data='gen:refs:skip')])
    rows.append([InlineKeyboardButton(text=t(lang, "ref_back_btn"), callback_data='gen:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_intro_text(models: list[Any], lang: str) -> str:
    lines = [
        t(lang, "model_intro_title"),
        t(lang, "model_intro_desc"),
        "",
        tf(lang, "model_intro_models", count=len(models)),
    ]
    for model in models:
        tagline = f' — {model.tagline}' if model.tagline else ''
        lines.append(f'• {model.display_name}{tagline}')
    lines.append("")
    lines.append(t(lang, "model_intro_select"))
    return '\n'.join(lines)


def _options_text(model, prompt: str, ref_count: int, lang: str) -> str:
    return (
        f"{t(lang, 'options_title')}\n"
        f"{tf(lang, 'options_model', model=model.display_name)}\n"
        f"{tf(lang, 'options_prompt', prompt=escape_html(prompt))}\n"
        f"{tf(lang, 'options_refs', count=ref_count)}\n\n"
        f"{t(lang, 'options_instruction')}"
    )


def _ref_label_for_model(model, lang: str) -> str:
    if model.requires_reference_images:
        return t(lang, "ref_label_photo")
    return t(lang, "ref_label_ref")


def _refs_prompt_text(count: int, max_refs: int, label: str, lang: str) -> str:
    if count <= 0:
        return tf(lang, "ref_prompt_initial", max=max_refs, label=label)
    if count < max_refs:
        return tf(lang, "ref_prompt_more", count=count, max=max_refs, label=label)
    return tf(lang, "ref_prompt_done", count=count, max=max_refs)


async def _safe_delete_by_id(chat_id: int, message_id: int, message: Message) -> None:
    try:
        await message.bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        return
    except Exception:
        return


def _render_options_panel(model, options: Dict[str, Any], outputs: int, lang: str) -> InlineKeyboardMarkup:
    settings = get_settings()
    return options_panel(model, options, outputs, settings.max_outputs_per_request, lang=lang)


@router.callback_query(F.data == 'gen:start')
async def gen_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "category_choose"), reply_markup=generate_category_menu(lang))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:category:image')
async def gen_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.choosing_model)
    models = [m for m in list_models() if m.model_type == 'image']
    lang = get_lang(callback.from_user)
    await callback.message.answer(_model_intro_text(models, lang), reply_markup=model_menu(models, lang))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:category:video')
async def gen_category_video(callback: CallbackQuery) -> None:
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "video_soon"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:back')
async def gen_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lang = get_lang(callback.from_user)
    await callback.message.answer(t(lang, "category_choose"), reply_markup=generate_category_menu(lang))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('gen:model:'))
async def gen_model(callback: CallbackQuery, state: FSMContext) -> None:
    model_key = callback.data.split(':', 2)[2]
    model = get_model(model_key)
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)
    await state.update_data(
        model_key=model_key,
        options={},
        outputs=1,
        ref_urls=[],
        ref_files=[],
        ref_token=None,
        ref_required=False,
        ref_help_msg_id=None,
    )

    if model.requires_reference_images:
        await state.set_state(GenerateFlow.collecting_refs)
        await state.update_data(ref_required=True)
        label = _ref_label_for_model(model, lang)
        text = _refs_prompt_text(0, model.max_reference_images, label, lang)
        msg = await callback.message.answer(text, reply_markup=_refs_menu(allow_skip=False, lang=lang))
        await state.update_data(ref_help_msg_id=msg.message_id)
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    if model.supports_reference_images:
        await state.set_state(GenerateFlow.choosing_ref_mode)
        await callback.message.answer(t(lang, "ref_optional"), reply_markup=ref_mode_menu(lang))
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(tf(lang, "prompt_enter", model=model.display_name))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.choosing_ref_mode, F.data.startswith('gen:refmode:'))
async def gen_ref_mode(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(':', 2)[2]
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)
    options: Dict[str, Any] = data.get('options', {})

    if choice == 'has':
        options['reference_images'] = 'has'
        await state.update_data(options=options, ref_required=False, ref_urls=[], ref_files=[], ref_token=None)
        await state.set_state(GenerateFlow.collecting_refs)
        label = _ref_label_for_model(model, lang)
        text = _refs_prompt_text(0, model.max_reference_images, label, lang)
        msg = await callback.message.answer(text, reply_markup=_refs_menu(allow_skip=True, lang=lang))
        await state.update_data(ref_help_msg_id=msg.message_id)
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    options['reference_images'] = 'none'
    await state.update_data(options=options, ref_urls=[], ref_files=[], ref_token=None)
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(tf(lang, "prompt_enter", model=model.display_name))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(GenerateFlow.entering_prompt)
async def gen_prompt(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    lang = get_lang(message.from_user)
    prompt = clamp_text(message.text or '', settings.max_prompt_length)
    if not prompt.strip():
        await message.answer(t(lang, "prompt_empty_bot"))
        return
    prompt_lower = prompt.lower()
    for term in settings.nsfw_terms():
        if term and term in prompt_lower:
            await message.answer(t(lang, "prompt_banned"))
            return

    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await message.answer(t(lang, "model_not_found"))
        return

    options = data.get('options', {})
    for opt in model.options:
        options.setdefault(opt.key, opt.default)
    options = model.validate_options(options)

    await state.update_data(prompt=prompt, options=options)
    await state.set_state(GenerateFlow.choosing_options)

    text = _options_text(model, prompt, len(data.get('ref_urls', [])), lang)
    await message.answer(text, reply_markup=_render_options_panel(model, options, int(data.get('outputs', 1)), lang))


@router.message(GenerateFlow.collecting_refs, F.photo)
async def collect_refs(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    lang = get_lang(message.from_user)
    if not model:
        await message.answer(t(lang, "model_not_found"))
        return

    ref_urls: List[str] = data.get('ref_urls', [])
    ref_files: List[str] = data.get('ref_files', [])
    ref_token = data.get('ref_token')

    max_refs = model.max_reference_images or settings.max_reference_images
    if len(ref_urls) >= max_refs:
        await message.answer(t(lang, "ref_limit"))
        return

    if not ref_token:
        ref_token = uuid.uuid4().hex
        await state.update_data(ref_token=ref_token)

    ref_dir = os.path.join(settings.reference_storage_path, ref_token)
    os.makedirs(ref_dir, exist_ok=True)

    file = await message.bot.get_file(message.photo[-1].file_id)
    ext = os.path.splitext(file.file_path or '')[1] or '.jpg'
    filename = f'{uuid.uuid4().hex}{ext}'
    local_path = os.path.join(ref_dir, filename)
    await message.bot.download_file(file.file_path, destination=local_path)

    public_url = f'{settings.public_file_base_url}/{ref_token}/{filename}'

    ref_urls.append(public_url)
    ref_files.append(local_path)
    await state.update_data(ref_urls=ref_urls, ref_files=ref_files)

    previous_id = data.get('ref_help_msg_id')
    if previous_id:
        await _safe_delete_by_id(message.chat.id, previous_id, message)

    label = _ref_label_for_model(model, lang)
    text = _refs_prompt_text(len(ref_urls), max_refs, label, lang)
    msg = await message.answer(text, reply_markup=_refs_menu(allow_skip=not data.get('ref_required'), lang=lang))
    await state.update_data(ref_help_msg_id=msg.message_id)


@router.message(GenerateFlow.collecting_refs, F.text)
async def collect_refs_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    lang = get_lang(message.from_user)
    if not model:
        await message.answer(t(lang, "ref_send_photo"))
        return

    previous_id = data.get('ref_help_msg_id')
    if previous_id:
        await _safe_delete_by_id(message.chat.id, previous_id, message)

    label = _ref_label_for_model(model, lang)
    text = _refs_prompt_text(len(data.get('ref_urls', [])), model.max_reference_images, label, lang)
    msg = await message.answer(text, reply_markup=_refs_menu(allow_skip=not data.get('ref_required'), lang=lang))
    await state.update_data(ref_help_msg_id=msg.message_id)


@router.callback_query(F.data == 'gen:refs:done')
async def refs_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)

    if data.get('ref_required') and not data.get('ref_urls'):
        await callback.answer(t(lang, "ref_required"), show_alert=True)
        return

    ref_help_id = data.get('ref_help_msg_id')
    if ref_help_id:
        await _safe_delete_by_id(callback.message.chat.id, ref_help_id, callback.message)
        await state.update_data(ref_help_msg_id=None)

    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(tf(lang, "prompt_enter", model=model.display_name))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:refs:skip')
async def refs_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get('ref_required'):
        await callback.answer(t(get_lang(callback.from_user), "ref_required_mode"), show_alert=True)
        return

    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)

    ref_help_id = data.get('ref_help_msg_id')
    if ref_help_id:
        await _safe_delete_by_id(callback.message.chat.id, ref_help_id, callback.message)
        await state.update_data(ref_help_msg_id=None)

    options = data.get('options', {})
    options['reference_images'] = 'none'
    await state.update_data(options=options, ref_urls=[], ref_files=[], ref_token=None)
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(tf(lang, "prompt_enter", model=model.display_name))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.choosing_options, F.data == 'gen:noop')
async def gen_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(GenerateFlow.choosing_options, F.data.startswith('gen:opt:'))
async def gen_option(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, key, value = callback.data.split(':', 3)
    data = await state.get_data()
    options: Dict[str, Any] = data.get('options', {})
    options[key] = value
    await state.update_data(options=options)

    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)

    text = _options_text(model, data.get('prompt', ''), len(data.get('ref_urls', [])), lang)
    await callback.message.edit_text(
        text,
        reply_markup=_render_options_panel(model, options, int(data.get('outputs', 1)), lang),
    )
    await callback.answer()


@router.callback_query(GenerateFlow.choosing_options, F.data.startswith('gen:outputs:'))
async def gen_outputs(callback: CallbackQuery, state: FSMContext) -> None:
    outputs = int(callback.data.split(':', 2)[2])
    await state.update_data(outputs=outputs)

    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)

    text = _options_text(model, data.get('prompt', ''), len(data.get('ref_urls', [])), lang)
    await callback.message.edit_text(
        text,
        reply_markup=_render_options_panel(model, data.get('options', {}), outputs, lang),
    )
    await callback.answer()


@router.callback_query(GenerateFlow.choosing_options, F.data == 'gen:options:next')
async def gen_options_next(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await _show_preview(callback, state, session)
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.choosing_options, F.data == 'gen:options:back')
async def gen_options_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(t(get_lang(callback.from_user), "prompt_enter_new"))
    await callback.answer()
    await safe_cleanup_callback(callback)


async def _show_preview(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)
    prompt = data.get('prompt', '')
    options = model.validate_options(data.get('options', {}))
    outputs = int(data.get('outputs', 1))
    ref_urls: List[str] = data.get('ref_urls', [])

    user = await _get_user(session, callback.from_user.id)
    discount = user.referral_discount_pct or 0 if user else 0
    pricing = PricingService(session)
    breakdown = await pricing.resolve_cost(model, options, outputs, discount)

    option_lines = _format_option_lines(model, options, lang)
    text = (
        f"{t(lang, 'preview_title')}\n"
        f"{tf(lang, 'preview_model', model=model.display_name)}\n"
        f"{tf(lang, 'preview_prompt', prompt=escape_html(prompt))}\n"
        f"{option_lines}"
        f"{tf(lang, 'preview_refs', count=len(ref_urls))}\n"
        f"{tf(lang, 'preview_outputs', count=outputs)}\n"
        f"{tf(lang, 'preview_cost_per', cost=breakdown.per_output)}\n"
        f"{tf(lang, 'preview_total', total=breakdown.total)}\n"
    )
    if discount:
        text += f"{tf(lang, 'preview_discount', pct=discount)}\n"
    text += f"\n{t(lang, 'preview_notice')}"
    await state.set_state(GenerateFlow.confirming)
    await callback.message.answer(text, reply_markup=confirm_menu(lang))
    await callback.answer()


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:confirm')
async def gen_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    lang = get_lang(callback.from_user)
    prompt = data.get('prompt', '')
    options = model.validate_options(data.get('options', {}))
    outputs = int(data.get('outputs', 1))
    ref_urls: List[str] = data.get('ref_urls', [])
    ref_files: List[str] = data.get('ref_files', [])

    if not rate_limiter.allow(callback.from_user.id):
        await callback.answer(t(lang, "error_too_many"), show_alert=True)
        return

    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.answer(t(lang, "error_generic"), show_alert=True)
        return

    kie = KieClient()
    gen_service = GenerationService(session, kie, callback.message.bot)
    try:
        generation = await gen_service.create_generation(user, model, prompt, options, outputs, ref_urls, ref_files)
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        msg = _error_text(str(exc), lang)
        await callback.message.answer(msg)
        await callback.answer()
        await safe_cleanup_callback(callback)
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
        await safe_cleanup_callback(callback)
        return
    finally:
        await kie.close()

    result = await session.execute(
        select(GenerationTask.id).where(GenerationTask.generation_id == generation.id)
    )
    task_ids = [row[0] for row in result.all()]
    poller = get_poller()
    if poller:
        for task_id in task_ids:
            poller.schedule(task_id)

    await _start_progress_message(session, generation, model.key, callback.message, lang)
    await callback.message.answer(t(lang, "task_started"))
    await state.clear()
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:edit:prompt')
async def gen_edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(t(get_lang(callback.from_user), "prompt_enter_new"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:edit:options')
async def gen_edit_options(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    await state.set_state(GenerateFlow.choosing_options)
    lang = get_lang(callback.from_user)
    text = _options_text(model, data.get('prompt', ''), len(data.get('ref_urls', [])), lang)
    await callback.message.answer(
        text,
        reply_markup=_render_options_panel(model, data.get('options', {}), int(data.get('outputs', 1)), lang),
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:cancel')
async def gen_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(t(get_lang(callback.from_user), "cancelled"))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:result:restart')
async def gen_result_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(GenerateFlow.choosing_model)
    models = [m for m in list_models() if m.model_type == 'image']
    lang = get_lang(callback.from_user)
    await callback.message.answer(_model_intro_text(models, lang), reply_markup=model_menu(models, lang))
    await callback.answer()


@router.callback_query(F.data == 'gen:result:finish')
async def gen_result_finish(callback: CallbackQuery) -> None:
    await callback.message.answer(t(get_lang(callback.from_user), "result_done"))
    await callback.answer()


@router.callback_query(F.data.startswith('gen:result:repeat:'))
async def gen_result_repeat(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 3)[3])
    gen = await session.get(Generation, gen_id)
    if not gen:
        await callback.answer(t(get_lang(callback.from_user), "error_generic"), show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer(t(get_lang(callback.from_user), "error_generic"), show_alert=True)
        return
    model = get_model(gen.model)
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return

    raw_options: Dict[str, Any] = dict(gen.options or {})
    raw_options.pop('reference_urls', None)
    raw_options.pop('reference_files', None)
    options = model.validate_options(raw_options)
    outputs = int(gen.outputs_requested or 1)
    discount = user.referral_discount_pct or 0

    pricing = PricingService(session)
    breakdown = await pricing.resolve_cost(model, options, outputs, discount)
    await callback.message.answer(
        tf(get_lang(callback.from_user), "repeat_prompt", cost=breakdown.total),
        reply_markup=repeat_confirm_menu(gen_id, get_lang(callback.from_user)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('gen:repeat:confirm:'))
async def gen_repeat_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 3)[3])
    gen = await session.get(Generation, gen_id)
    if not gen:
        await callback.answer(t(get_lang(callback.from_user), "error_generic"), show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer(t(get_lang(callback.from_user), "error_generic"), show_alert=True)
        return
    model = get_model(gen.model)
    if not model:
        await callback.answer(t(get_lang(callback.from_user), "model_not_found"), show_alert=True)
        return
    if not rate_limiter.allow(callback.from_user.id):
        await callback.answer(t(get_lang(callback.from_user), "error_too_many"), show_alert=True)
        return

    raw_options: Dict[str, Any] = dict(gen.options or {})
    ref_urls = raw_options.pop('reference_urls', []) or []
    raw_options.pop('reference_files', None)
    options = model.validate_options(raw_options)
    outputs = int(gen.outputs_requested or 1)

    kie = KieClient()
    gen_service = GenerationService(session, kie, callback.message.bot)
    try:
        new_gen = await gen_service.create_generation(
            user,
            model,
            gen.prompt,
            options,
            outputs,
            ref_urls,
            None,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        msg = _error_text(str(exc), get_lang(callback.from_user))
        await callback.message.answer(msg)
        await callback.answer()
        return
    except KieError as exc:
        await session.commit()
        if exc.status_code == 429:
            queue_pos = await _queue_position(session)
            await callback.message.answer(tf(get_lang(callback.from_user), "queued", pos=queue_pos))
        elif exc.status_code in (401, 402):
            await callback.message.answer(t(get_lang(callback.from_user), "api_auth_error"))
        else:
            await callback.message.answer(t(get_lang(callback.from_user), "create_failed"))
        await callback.answer()
        return
    finally:
        await kie.close()

    result = await session.execute(
        select(GenerationTask.id).where(GenerationTask.generation_id == new_gen.id)
    )
    task_ids = [row[0] for row in result.all()]
    poller = get_poller()
    if poller:
        for task_id in task_ids:
            poller.schedule(task_id)

    await _start_progress_message(session, new_gen, model.key, callback.message, get_lang(callback.from_user))
    await callback.message.answer(t(get_lang(callback.from_user), "task_started"))
    await callback.answer()


@router.callback_query(F.data == 'gen:repeat:cancel')
async def gen_repeat_cancel(callback: CallbackQuery) -> None:
    await callback.answer(t(get_lang(callback.from_user), "repeat_cancelled"))


def _format_option_lines(model, options: Dict[str, Any], lang: str) -> str:
    lines = []
    icon_map = {
        'output_format': '🖼️',
        'image_size': '📐',
        'aspect_ratio': '📐',
        'resolution': '🧩',
    }
    for opt in model.options:
        if opt.ui_hidden:
            continue
        selected = options.get(opt.key, opt.default)
        fallback_label = next((v.label for v in opt.values if v.value == selected), selected)
        label = _value_label(opt.key, str(selected), str(fallback_label), lang)
        label = escape_html(str(label))
        opt_label = _option_label(opt.key, opt.label, lang)
        icon = icon_map.get(opt.key, '⚙️')
        lines.append(f'{icon} {opt_label}: {label}\n')
    return ''.join(lines)


def _option_label(key: str, fallback: str, lang: str) -> str:
    if key in ("image_size", "aspect_ratio"):
        return t(lang, "aspect_ratio")
    if key == "output_format":
        return t(lang, "output_format")
    if key == "resolution":
        return t(lang, "resolution")
    if key == "reference_images":
        return t(lang, "upload_label")
    return fallback


def _value_label(key: str, value: str, fallback: str, lang: str) -> str:
    if key in ("image_size", "aspect_ratio"):
        ratio_key = value.replace(":", "_").lower()
        return t(lang, f"ratio_{ratio_key}")
    if key == "resolution":
        res_key = value.lower()
        return t(lang, f"res_{res_key}")
    if key == "output_format":
        return value.upper()
    if key == "reference_images":
        if value == "none":
            return t(lang, "ref_mode_none")
        if value == "has":
            return t(lang, "ref_mode_has")
    return fallback


async def _queue_position(session: AsyncSession) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count(GenerationTask.id)).where(GenerationTask.state.in_(['queued', 'pending', 'running']))
    )
    return int(result.scalar_one() or 0)


async def _get_user(session: AsyncSession, telegram_id: int):
    from app.services.credits import CreditsService

    credits = CreditsService(session)
    return await credits.get_user(telegram_id)


def _error_text(code: str, lang: str) -> str:
    mapping = {
        'banned': 'error_banned',
        'outputs': 'error_outputs',
        'too_many': 'error_too_many',
        'daily_cap': 'error_daily_cap',
        'no_credits': 'error_no_credits',
        'refs_required': 'error_refs_required',
    }
    key = mapping.get(code, 'error_generic')
    return t(lang, key)


async def _start_progress_message(
    session: AsyncSession,
    generation: Generation,
    model_key: str,
    message: Message,
    lang: str,
) -> None:
    try:
        progress_msg = await message.answer(tf(lang, "progress_label", pct=0))
    except Exception:
        return

    generation.progress_message_id = progress_msg.message_id
    generation.updated_at = utcnow()
    await session.commit()

    poller = get_poller()
    sessionmaker = poller.sessionmaker if poller else create_sessionmaker()
    bot = poller.bot if poller else message.bot
    progress = ProgressService(bot, sessionmaker)
    progress.start(
        generation_id=generation.id,
        chat_id=message.chat.id,
        message_id=progress_msg.message_id,
        model_key=model_key,
        lang=lang,
    )

