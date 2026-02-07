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
from app.modelspecs.registry import get_model, list_models
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.poller_runtime import get_poller
from app.services.pricing import PricingService
from app.services.rate_limit import RateLimiter
from app.utils.text import escape_html, clamp_text


router = Router()
rate_limiter = RateLimiter(get_settings().per_user_generate_cooldown_seconds)


def _refs_menu(allow_skip: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='✅ Готово', callback_data='gen:refs:done')]]
    if allow_skip:
        rows.append([InlineKeyboardButton(text='⏭️ Пропустить', callback_data='gen:refs:skip')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_intro_text(models: list[Any]) -> str:
    lines = [
        '🖼️ <b>Gemini Images</b>',
        'Создавайте и редактируйте изображения прямо в чате.',
        '',
        f'Для вас работают {len(models)} модели:',
    ]
    for model in models:
        tagline = f' — {model.tagline}' if model.tagline else ''
        lines.append(f'• {model.display_name}{tagline}')
    lines.append('')
    lines.append('Выберите модель ниже:')
    return '\n'.join(lines)


def _options_text(model, prompt: str, ref_count: int) -> str:
    return (
        '⚙️ <b>Параметры генерации</b>\n'
        f'🧠 Модель: {model.display_name}\n'
        f'✍️ Промпт: {escape_html(prompt)}\n'
        f'📎 Референсов: {ref_count}\n\n'
        'Отметьте нужные параметры и нажмите «Далее».'
    )


def _ref_label_for_model(model) -> str:
    if model.requires_reference_images:
        return 'фото'
    return 'референс-фото'


def _refs_prompt_text(count: int, max_refs: int, label: str) -> str:
    if count <= 0:
        return (
            f'📎 Отправьте до {max_refs} {label}.\n'
            'Когда закончите, нажмите «Готово».'
        )
    if count < max_refs:
        return (
            f'✅ Добавлено {count} из {max_refs} фото.\n'
            f'Пришлите еще {label} или нажмите «Готово».'
        )
    return f'✅ Добавлено {count} из {max_refs} фото.\nНажмите «Готово».'


async def _safe_delete_by_id(chat_id: int, message_id: int, message: Message) -> None:
    try:
        await message.bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        return
    except Exception:
        return


def _render_options_panel(model, options: Dict[str, Any], outputs: int) -> InlineKeyboardMarkup:
    settings = get_settings()
    return options_panel(model, options, outputs, settings.max_outputs_per_request)


@router.callback_query(F.data == 'gen:start')
async def gen_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer('📂 Выберите категорию:', reply_markup=generate_category_menu())
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:category:image')
async def gen_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.choosing_model)
    models = [m for m in list_models() if m.model_type == 'image']
    await callback.message.answer(_model_intro_text(models), reply_markup=model_menu(models))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:category:video')
async def gen_category_video(callback: CallbackQuery) -> None:
    await callback.message.answer('🎬 Видео пока недоступно. Скоро добавим.')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:back')
async def gen_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer('🏠 Главное меню', reply_markup=generate_category_menu())
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('gen:model:'))
async def gen_model(callback: CallbackQuery, state: FSMContext) -> None:
    model_key = callback.data.split(':', 2)[2]
    model = get_model(model_key)
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
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
        label = _ref_label_for_model(model)
        text = _refs_prompt_text(0, model.max_reference_images, label)
        msg = await callback.message.answer(text, reply_markup=_refs_menu(allow_skip=False))
        await state.update_data(ref_help_msg_id=msg.message_id)
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    if model.supports_reference_images:
        await state.set_state(GenerateFlow.choosing_ref_mode)
        await callback.message.answer(
            '📎 Можно добавить референсы для более точного результата.\n'
            'Хотите использовать референсы?',
            reply_markup=ref_mode_menu(),
        )
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(f'✍️ Введите промпт для {model.display_name}:')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.choosing_ref_mode, F.data.startswith('gen:refmode:'))
async def gen_ref_mode(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(':', 2)[2]
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    options: Dict[str, Any] = data.get('options', {})

    if choice == 'has':
        options['reference_images'] = 'has'
        await state.update_data(options=options, ref_required=False, ref_urls=[], ref_files=[], ref_token=None)
        await state.set_state(GenerateFlow.collecting_refs)
        label = _ref_label_for_model(model)
        text = _refs_prompt_text(0, model.max_reference_images, label)
        msg = await callback.message.answer(text, reply_markup=_refs_menu(allow_skip=True))
        await state.update_data(ref_help_msg_id=msg.message_id)
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    options['reference_images'] = 'none'
    await state.update_data(options=options, ref_urls=[], ref_files=[], ref_token=None)
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(f'✍️ Введите промпт для {model.display_name}:')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(GenerateFlow.entering_prompt)
async def gen_prompt(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    prompt = clamp_text(message.text or '', settings.max_prompt_length)
    if not prompt.strip():
        await message.answer('Промпт не может быть пустым. Попробуйте еще раз.')
        return
    prompt_lower = prompt.lower()
    for term in settings.nsfw_terms():
        if term and term in prompt_lower:
            await message.answer('Запрос содержит запрещенные слова. Измените промпт.')
            return

    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await message.answer('Модель не найдена. Начните заново /start.')
        return

    options = data.get('options', {})
    for opt in model.options:
        options.setdefault(opt.key, opt.default)
    options = model.validate_options(options)

    await state.update_data(prompt=prompt, options=options)
    await state.set_state(GenerateFlow.choosing_options)

    text = _options_text(model, prompt, len(data.get('ref_urls', [])))
    await message.answer(text, reply_markup=_render_options_panel(model, options, int(data.get('outputs', 1))))


@router.message(GenerateFlow.collecting_refs, F.photo)
async def collect_refs(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await message.answer('Модель не найдена. Начните заново /start.')
        return

    ref_urls: List[str] = data.get('ref_urls', [])
    ref_files: List[str] = data.get('ref_files', [])
    ref_token = data.get('ref_token')

    max_refs = model.max_reference_images or settings.max_reference_images
    if len(ref_urls) >= max_refs:
        await message.answer('Достигнут лимит референс-изображений.')
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

    label = _ref_label_for_model(model)
    text = _refs_prompt_text(len(ref_urls), max_refs, label)
    msg = await message.answer(text, reply_markup=_refs_menu(allow_skip=not data.get('ref_required')))
    await state.update_data(ref_help_msg_id=msg.message_id)


@router.message(GenerateFlow.collecting_refs, F.text)
async def collect_refs_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await message.answer('Пожалуйста, отправьте фото или нажмите «Готово».')
        return

    previous_id = data.get('ref_help_msg_id')
    if previous_id:
        await _safe_delete_by_id(message.chat.id, previous_id, message)

    label = _ref_label_for_model(model)
    text = _refs_prompt_text(len(data.get('ref_urls', [])), model.max_reference_images, label)
    msg = await message.answer(text, reply_markup=_refs_menu(allow_skip=not data.get('ref_required')))
    await state.update_data(ref_help_msg_id=msg.message_id)


@router.callback_query(F.data == 'gen:refs:done')
async def refs_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return

    if data.get('ref_required') and not data.get('ref_urls'):
        await callback.answer('Нужно добавить хотя бы одно фото.', show_alert=True)
        return

    ref_help_id = data.get('ref_help_msg_id')
    if ref_help_id:
        await _safe_delete_by_id(callback.message.chat.id, ref_help_id, callback.message)
        await state.update_data(ref_help_msg_id=None)

    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(f'✍️ Введите промпт для {model.display_name}:')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:refs:skip')
async def refs_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get('ref_required'):
        await callback.answer('Для этого режима нужны референсы.', show_alert=True)
        return

    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return

    ref_help_id = data.get('ref_help_msg_id')
    if ref_help_id:
        await _safe_delete_by_id(callback.message.chat.id, ref_help_id, callback.message)
        await state.update_data(ref_help_msg_id=None)

    options = data.get('options', {})
    options['reference_images'] = 'none'
    await state.update_data(options=options, ref_urls=[], ref_files=[], ref_token=None)
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(f'✍️ Введите промпт для {model.display_name}:')
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
        await callback.answer('Модель не найдена', show_alert=True)
        return

    text = _options_text(model, data.get('prompt', ''), len(data.get('ref_urls', [])))
    await callback.message.edit_text(
        text,
        reply_markup=_render_options_panel(model, options, int(data.get('outputs', 1))),
    )
    await callback.answer()


@router.callback_query(GenerateFlow.choosing_options, F.data.startswith('gen:outputs:'))
async def gen_outputs(callback: CallbackQuery, state: FSMContext) -> None:
    outputs = int(callback.data.split(':', 2)[2])
    await state.update_data(outputs=outputs)

    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return

    text = _options_text(model, data.get('prompt', ''), len(data.get('ref_urls', [])))
    await callback.message.edit_text(
        text,
        reply_markup=_render_options_panel(model, data.get('options', {}), outputs),
    )
    await callback.answer()


@router.callback_query(GenerateFlow.choosing_options, F.data == 'gen:options:next')
async def gen_options_next(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await _show_preview(callback, state, session)
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.choosing_options, F.data == 'gen:options:back')
async def gen_options_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer('✍️ Введите новый промпт:')
    await callback.answer()
    await safe_cleanup_callback(callback)


async def _show_preview(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    prompt = data.get('prompt', '')
    options = model.validate_options(data.get('options', {}))
    outputs = int(data.get('outputs', 1))
    ref_urls: List[str] = data.get('ref_urls', [])

    user = await _get_user(session, callback.from_user.id)
    discount = user.referral_discount_pct or 0 if user else 0
    pricing = PricingService(session)
    breakdown = await pricing.resolve_cost(model, options, outputs, discount)

    option_lines = _format_option_lines(model, options)
    text = (
        f'✅ <b>Проверьте стоимость</b>\n'
        f'🧠 Модель: {model.display_name}\n'
        f'✍️ Промпт: {escape_html(prompt)}\n'
        f'{option_lines}'
        f'📎 Референсов: {len(ref_urls)}\n'
        f'🔢 Выходов: {outputs}\n'
        f'💳 Цена за 1: {breakdown.per_output} кр.\n'
        f'🧾 Итого: {breakdown.total} кр.\n'
    )
    if discount:
        text += f'Скидка: {discount}%\n'
    text += '\nПодтверждая, вы соглашаетесь соблюдать закон и правила сервиса.'
    await state.set_state(GenerateFlow.confirming)
    await callback.message.answer(text, reply_markup=confirm_menu())
    await callback.answer()


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:confirm')
async def gen_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    prompt = data.get('prompt', '')
    options = model.validate_options(data.get('options', {}))
    outputs = int(data.get('outputs', 1))
    ref_urls: List[str] = data.get('ref_urls', [])
    ref_files: List[str] = data.get('ref_files', [])

    if not rate_limiter.allow(callback.from_user.id):
        await callback.answer('Слишком часто. Подождите пару секунд.', show_alert=True)
        return

    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return

    kie = KieClient()
    gen_service = GenerationService(session, kie, callback.message.bot)
    try:
        generation = await gen_service.create_generation(user, model, prompt, options, outputs, ref_urls, ref_files)
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        msg = _error_text(str(exc))
        await callback.message.answer(msg)
        await callback.answer()
        await safe_cleanup_callback(callback)
        return
    except KieError as exc:
        await session.commit()
        if exc.status_code == 429:
            queue_pos = await _queue_position(session)
            await callback.message.answer(
                f'Сервис перегружен, задача поставлена в очередь. Примерная позиция: {queue_pos}.'
            )
        elif exc.status_code in (401, 402):
            await callback.message.answer('Ошибка доступа к API. Проверьте ключ Kie.ai.')
        else:
            await callback.message.answer('Не удалось создать задачу. Попробуйте позже.')
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

    await callback.message.answer('Задача запущена. Как только будет готово - отправлю результат.')
    await state.clear()
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:edit:prompt')
async def gen_edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer('✍️ Введите новый промпт:')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:edit:options')
async def gen_edit_options(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    await state.set_state(GenerateFlow.choosing_options)
    text = _options_text(model, data.get('prompt', ''), len(data.get('ref_urls', [])))
    await callback.message.answer(
        text,
        reply_markup=_render_options_panel(model, data.get('options', {}), int(data.get('outputs', 1))),
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:cancel')
async def gen_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer('❌ Отменено.')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'gen:result:restart')
async def gen_result_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(GenerateFlow.choosing_model)
    models = [m for m in list_models() if m.model_type == 'image']
    await callback.message.answer(_model_intro_text(models), reply_markup=model_menu(models))
    await callback.answer()


@router.callback_query(F.data == 'gen:result:finish')
async def gen_result_finish(callback: CallbackQuery) -> None:
    await callback.message.answer('✅ Завершено. Если хотите еще — нажмите /start.')
    await callback.answer()


@router.callback_query(F.data.startswith('gen:result:repeat:'))
async def gen_result_repeat(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 3)[3])
    gen = await session.get(Generation, gen_id)
    if not gen:
        await callback.answer('Не найдено', show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    model = get_model(gen.model)
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
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
        f'Отправить запрос повторно?\nБудет списано {breakdown.total} кредитов.',
        reply_markup=repeat_confirm_menu(gen_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('gen:repeat:confirm:'))
async def gen_repeat_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 3)[3])
    gen = await session.get(Generation, gen_id)
    if not gen:
        await callback.answer('Не найдено', show_alert=True)
        return
    user = await _get_user(session, callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    model = get_model(gen.model)
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    if not rate_limiter.allow(callback.from_user.id):
        await callback.answer('Слишком часто. Подождите пару секунд.', show_alert=True)
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
        msg = _error_text(str(exc))
        await callback.message.answer(msg)
        await callback.answer()
        return
    except KieError as exc:
        await session.commit()
        if exc.status_code == 429:
            queue_pos = await _queue_position(session)
            await callback.message.answer(
                f'Сервис перегружен, задача поставлена в очередь. Примерная позиция: {queue_pos}.'
            )
        elif exc.status_code in (401, 402):
            await callback.message.answer('Ошибка доступа к API. Проверьте ключ Kie.ai.')
        else:
            await callback.message.answer('Не удалось создать задачу. Попробуйте позже.')
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

    await callback.message.answer('Задача запущена. Как только будет готово - отправлю результат.')
    await callback.answer()


@router.callback_query(F.data == 'gen:repeat:cancel')
async def gen_repeat_cancel(callback: CallbackQuery) -> None:
    await callback.answer('Отменено.')


def _format_option_lines(model, options: Dict[str, Any]) -> str:
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
        label = next((v.label for v in opt.values if v.value == selected), selected)
        label = escape_html(str(label))
        icon = icon_map.get(opt.key, '⚙️')
        lines.append(f'{icon} {opt.label}: {label}\n')
    return ''.join(lines)


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


def _error_text(code: str) -> str:
    mapping = {
        'banned': 'Доступ запрещен.',
        'outputs': 'Недопустимое число вариантов.',
        'too_many': 'Слишком много активных задач. Подождите завершения текущих.',
        'daily_cap': 'Достигнут дневной лимит расходов.',
        'no_credits': 'Недостаточно кредитов. Купите пакет.',
        'refs_required': 'Для этого режима нужно добавить хотя бы одно фото.',
    }
    return mapping.get(code, 'Не удалось запустить генерацию.')

