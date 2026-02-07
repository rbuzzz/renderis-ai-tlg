from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Generation, GenerationTask
from app.modelspecs.registry import get_model
from app.services.credits import CreditsService
from app.services.generation import GenerationService
from app.services.kie_client import KieClient
from app.services.poller_runtime import get_poller
from app.bot.utils import safe_cleanup_callback
from app.utils.text import escape_html


router = Router()


@router.callback_query(F.data == 'history:list')
async def history_list(callback: CallbackQuery, session: AsyncSession) -> None:
    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if not user:
        await callback.message.answer('Пользователь не найден.')
        await callback.answer()
        return

    result = await session.execute(
        select(Generation)
        .where(Generation.user_id == user.id)
        .order_by(Generation.created_at.desc())
        .limit(10)
    )
    items = list(result.scalars().all())
    if not items:
        await callback.message.answer('🕘 История пуста.')
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    for gen in items:
        text = (
            f'<b>{gen.model}</b> | {gen.status}\n'
            f'Промпт: {escape_html(gen.prompt[:200])}\n'
            f'Создано: {gen.created_at:%Y-%m-%d %H:%M}'
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text='Открыть результаты', callback_data=f'history:open:{gen.id}'),
                    InlineKeyboardButton(text='Регенерировать', callback_data=f'history:regen:{gen.id}'),
                ]
            ]
        )
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('history:open:'))
async def history_open(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 2)[2])
    gen = await session.get(Generation, gen_id)
    if not gen:
        await callback.answer('Не найдено', show_alert=True)
        return

    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer('Недостаточно прав', show_alert=True)
        return

    result = await session.execute(
        select(GenerationTask).where(GenerationTask.generation_id == gen_id, GenerationTask.state == 'success')
    )
    tasks = list(result.scalars().all())
    if not tasks:
        await callback.message.answer('⏳ Результаты еще не готовы.')
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    urls = []
    for task in tasks:
        urls.extend(task.result_urls or [])
    if not urls:
        await callback.message.answer('⚠️ Ссылки не найдены.')
        await callback.answer()
        await safe_cleanup_callback(callback)
        return
    if len(urls) == 1:
        await callback.message.answer_photo(urls[0])
    else:
        media = [InputMediaPhoto(media=u) for u in urls[:10]]
        await callback.message.answer_media_group(media=media)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('history:regen:'))
async def history_regen(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 2)[2])
    gen = await session.get(Generation, gen_id)
    if not gen:
        await callback.answer('Не найдено', show_alert=True)
        return

    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer('Недостаточно прав', show_alert=True)
        return

    model = get_model(gen.model)
    if not model:
        await callback.answer('Модель не найдена', show_alert=True)
        return

    kie = KieClient()
    service = GenerationService(session, kie, callback.message.bot)
    new_gen = await service.create_generation(user, model, gen.prompt, gen.options or {}, gen.outputs_requested)
    await session.commit()
    await kie.close()

    result = await session.execute(
        select(GenerationTask.id).where(GenerationTask.generation_id == new_gen.id)
    )
    task_ids = [row[0] for row in result.all()]
    poller = get_poller()
    if poller:
        for task_id in task_ids:
            poller.schedule(task_id)

    await callback.message.answer('✅ Регенерация запущена.')
    await callback.answer()
    await safe_cleanup_callback(callback)
