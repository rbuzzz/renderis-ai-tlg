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
from app.bot.i18n import get_lang, t, tf
from app.utils.credits import credits_to_display
from app.utils.text import escape_html


router = Router()


def _history_actions_menu(generation_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "history_open_results"), callback_data=f"history:open:{generation_id}")],
            [
                InlineKeyboardButton(text=t(lang, "result_repeat"), callback_data=f"gen:result:repeat:{generation_id}"),
                InlineKeyboardButton(text=t(lang, "result_edit_ai"), callback_data=f"gen:result:editai:{generation_id}"),
            ],
            [InlineKeyboardButton(text=t(lang, "result_upscale"), callback_data=f"gen:result:upscale:{generation_id}")],
        ]
    )


async def _first_success_url(session: AsyncSession, generation_id: int) -> str | None:
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


@router.callback_query(F.data == 'history:list')
async def history_list(callback: CallbackQuery, session: AsyncSession) -> None:
    credits = CreditsService(session)
    lang = get_lang(callback.from_user)
    user = await credits.get_user(callback.from_user.id)
    if not user:
        await callback.message.answer(t(lang, "history_user_not_found"))
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
        await callback.message.answer(t(lang, "history_empty_bot"))
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    for gen in items:
        model = get_model(gen.model)
        model_label = model.display_name if model else gen.model
        preview_url = await _first_success_url(session, gen.id)
        text = (
            f'<b>{t(lang, "history_timeline_title")}</b>\n'
            f'{tf(lang, "history_model", model=escape_html(model_label))}\n'
            f'{tf(lang, "history_status", status=gen.status)}\n'
            f'{tf(lang, "history_cost", cost=credits_to_display(gen.final_cost_credits))}\n'
            f'{t(lang, "prompt_label")}: {escape_html(gen.prompt[:200])}\n'
            f'{t(lang, "history_created")}: {gen.created_at:%Y-%m-%d %H:%M}'
        )
        keyboard = _history_actions_menu(gen.id, lang)
        if preview_url:
            try:
                await callback.message.answer_photo(preview_url, caption=text, reply_markup=keyboard)
                continue
            except Exception:
                pass
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('history:open:'))
async def history_open(callback: CallbackQuery, session: AsyncSession) -> None:
    gen_id = int(callback.data.split(':', 2)[2])
    gen = await session.get(Generation, gen_id)
    lang = get_lang(callback.from_user)
    if not gen:
        await callback.answer(t(lang, "history_not_found"), show_alert=True)
        return

    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer(t(lang, "history_no_access"), show_alert=True)
        return

    result = await session.execute(
        select(GenerationTask).where(GenerationTask.generation_id == gen_id, GenerationTask.state == 'success')
    )
    tasks = list(result.scalars().all())
    if not tasks:
        await callback.message.answer(t(lang, "history_not_ready"))
        await callback.answer()
        await safe_cleanup_callback(callback)
        return

    urls = []
    for task in tasks:
        urls.extend(task.result_urls or [])
    if not urls:
        await callback.message.answer(t(lang, "history_links_missing"))
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
    lang = get_lang(callback.from_user)
    if not gen:
        await callback.answer(t(lang, "history_not_found"), show_alert=True)
        return

    credits = CreditsService(session)
    user = await credits.get_user(callback.from_user.id)
    if not user or gen.user_id != user.id:
        await callback.answer(t(lang, "history_no_access"), show_alert=True)
        return

    model = get_model(gen.model)
    if not model:
        await callback.answer(t(lang, "model_not_found"), show_alert=True)
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

    await callback.message.answer(t(lang, "history_regen_started"))
    await callback.answer()
    await safe_cleanup_callback(callback)
