from __future__ import annotations

from typing import Any, Dict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.main import confirm_menu, generate_category_menu, model_menu, option_menu, outputs_menu
from app.bot.states import GenerateFlow
from app.config import get_settings
from app.db.models import GenerationTask
from app.modelspecs.registry import get_model, list_models
from app.services.generation import GenerationService
from app.services.kie_client import KieClient, KieError
from app.services.pricing import PricingService
from app.services.rate_limit import RateLimiter
from app.utils.text import escape_html, clamp_text


router = Router()
rate_limiter = RateLimiter(get_settings().per_user_generate_cooldown_seconds)


@router.callback_query(F.data == 'gen:start')
async def gen_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer('Выберите категорию:', reply_markup=generate_category_menu())
    await callback.answer()


@router.callback_query(F.data == 'gen:category:image')
async def gen_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.choosing_model)
    await callback.message.answer('Выберите модель:', reply_markup=model_menu(list_models()))
    await callback.answer()


@router.callback_query(F.data == 'gen:category:video')
async def gen_category_video(callback: CallbackQuery) -> None:
    await callback.message.answer('Видео пока недоступно. Скоро добавим.')
    await callback.answer()


@router.callback_query(F.data == 'gen:back')
async def gen_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer('Главное меню', reply_markup=generate_category_menu())
    await callback.answer()


@router.callback_query(F.data.startswith('gen:model:'))
async def gen_model(callback: CallbackQuery, state: FSMContext) -> None:
    model_key = callback.data.split(':', 2)[2]
    model = get_model(model_key)
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    await state.update_data(model_key=model_key, options={}, opt_index=0, outputs=1)
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer(f'Введите промпт для {model.display_name}:')
    if model.supports_reference_images:
        await callback.message.answer('Референс-изображения временно недоступны. Скоро добавим.')
    await callback.answer()


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
    options = {opt.key: opt.default for opt in model.options}
    await state.update_data(prompt=prompt, options=options, opt_index=0)
    await state.set_state(GenerateFlow.choosing_options)
    await _ask_option(message, state)


async def _ask_option(message_or_callback: Any, state: FSMContext) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        return
    idx = int(data.get('opt_index', 0))
    if idx >= len(model.options):
        await state.set_state(GenerateFlow.choosing_outputs)
        outputs = int(data.get('outputs', 1))
        await message_or_callback.answer(
            'Сколько вариантов сгенерировать?',
            reply_markup=outputs_menu(get_settings().max_outputs_per_request, outputs),
        )
        return
    opt = model.options[idx]
    selected = data.get('options', {}).get(opt.key, opt.default)
    await message_or_callback.answer(f'Выберите: {opt.label}', reply_markup=option_menu(opt, selected))


@router.callback_query(F.data.startswith('gen:opt:'))
async def gen_option(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, key, value = callback.data.split(':', 3)
    data = await state.get_data()
    options: Dict[str, Any] = data.get('options', {})
    options[key] = value
    await state.update_data(options=options, opt_index=int(data.get('opt_index', 0)) + 1)
    await _ask_option(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == 'gen:options:back')
async def gen_option_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    idx = max(int(data.get('opt_index', 0)) - 1, 0)
    await state.update_data(opt_index=idx)
    await _ask_option(callback.message, state)
    await callback.answer()


@router.callback_query(F.data.startswith('gen:outputs:'))
async def gen_outputs(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    value = callback.data.split(':', 2)[2]
    if value == 'back':
        await state.set_state(GenerateFlow.choosing_options)
        await _ask_option(callback.message, state)
        await callback.answer()
        return
    outputs = int(value)
    await state.update_data(outputs=outputs)
    await _show_preview(callback, state, session)


async def _show_preview(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    model = get_model(data.get('model_key', ''))
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return
    prompt = data.get('prompt', '')
    options = model.validate_options(data.get('options', {}))
    outputs = int(data.get('outputs', 1))

    user = await _get_user(session, callback.from_user.id)
    discount = user.referral_discount_pct or 0 if user else 0
    pricing = PricingService(session)
    breakdown = await pricing.resolve_cost(model, options, outputs, discount)

    text = (
        f'<b>Проверьте стоимость</b>\n'
        f'Модель: {model.display_name}\n'
        f'Промпт: {escape_html(prompt)}\n'
        f'Опции: {escape_html(str(options))}\n'
        f'Выходов: {outputs}\n'
        f'Цена за 1: {breakdown.per_output} кр.\n'
        f'Итого: {breakdown.total} кр.\n'
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

    if not rate_limiter.allow(callback.from_user.id):
        await callback.answer('Слишком часто. Подождите пару секунд.', show_alert=True)
        return

    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return

    kie = KieClient()
    gen_service = GenerationService(session, kie)
    try:
        generation = await gen_service.create_generation(user, model, prompt, options, outputs)
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
        select(GenerationTask.id).where(GenerationTask.generation_id == generation.id)
    )
    task_ids = [row[0] for row in result.all()]
    poller = callback.bot.get('poller')
    if poller:
        for task_id in task_ids:
            poller.schedule(task_id)

    await callback.message.answer('Задача запущена. Как только будет готово - отправлю результат.')
    await state.clear()
    await callback.answer()


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:edit:prompt')
async def gen_edit_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GenerateFlow.entering_prompt)
    await callback.message.answer('Введите новый промпт:')
    await callback.answer()


@router.callback_query(GenerateFlow.confirming, F.data == 'gen:edit:options')
async def gen_edit_options(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(opt_index=0)
    await state.set_state(GenerateFlow.choosing_options)
    await _ask_option(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == 'gen:cancel')
async def gen_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer('Отменено.')
    await callback.answer()


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
    }
    return mapping.get(code, 'Не удалось запустить генерацию.')
