from __future__ import annotations

import tempfile
import uuid
import asyncio

from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.admin import admin_menu
from app.bot.utils import safe_cleanup_callback
from app.bot.states import AdminFlow
from app.config import get_settings
from app.db.models import User
from app.modelspecs.registry import get_model
from app.services.analytics import AnalyticsService
from app.services.credits import CreditsService
from app.services.pricing import PricingService
from app.services.promos import PromoService
from app.services.referrals import ReferralService
from app.services.support import SupportService
from app.utils.logging import get_logger


router = Router()
logger = get_logger("bot-admin")


async def _is_admin(session: AsyncSession, telegram_id: int) -> bool:
    settings = get_settings()
    if settings.is_admin_telegram_id(telegram_id):
        return True
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    return bool(user and user.is_admin)


async def _active_user_telegram_ids(session: AsyncSession) -> list[int]:
    rows = await session.execute(
        select(User.telegram_id).where(
            User.is_admin.is_(False),
            User.is_banned.is_(False),
            User.last_seen_at.is_not(None),
        )
    )
    return [int(row[0]) for row in rows.all() if row[0]]


@router.message(Command('admin'))
async def admin_menu_cmd(message: Message, session: AsyncSession) -> None:
    if not await _is_admin(session, message.from_user.id):
        await message.answer('Недостаточно прав.')
        return
    await message.answer('🛠️ Админ-панель:', reply_markup=admin_menu())


@router.message(Command('broadcast'))
async def admin_broadcast_cmd(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not await _is_admin(session, message.from_user.id):
        await message.answer('Недостаточно прав.')
        return
    await state.set_state(AdminFlow.broadcast_message)
    await message.answer(
        '📣 Режим рассылки включен.\n'
        'Отправьте одним сообщением текст или медиа для рассылки всем активным пользователям.\n'
        'Для отмены: /cancel'
    )


@router.callback_query(F.data == 'admin:broadcast')
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.broadcast_message)
    await callback.message.answer(
        '📣 Режим рассылки включен.\n'
        'Отправьте одним сообщением текст или медиа для рассылки всем активным пользователям.\n'
        'Для отмены: /cancel'
    )
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.broadcast_message, Command('cancel'))
async def admin_broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer('Рассылка отменена.')


@router.message(AdminFlow.broadcast_message)
async def admin_broadcast_send(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, message.from_user.id):
        await state.clear()
        await message.answer('Недостаточно прав.')
        return

    recipients = await _active_user_telegram_ids(session)
    if not recipients:
        await state.clear()
        await message.answer('Нет активных пользователей для рассылки.')
        return

    sent_ok = 0
    sent_fail = 0
    fail_details: list[str] = []
    for telegram_id in recipients:
        try:
            await message.bot.copy_message(
                chat_id=telegram_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent_ok += 1
        except Exception as e:
            sent_fail += 1
            err = f"{type(e).__name__}: {e}"
            fail_details.append(f"{telegram_id} -> {err}")
            logger.warning("broadcast_failed", telegram_id=telegram_id, error=err)
        await asyncio.sleep(0.05)

    await state.clear()
    details_text = ""
    if fail_details:
        details_text = "\n\nПервые ошибки:\n" + "\n".join(fail_details[:5])
    await message.answer(
        f'Рассылка завершена.\n'
        f'Успешно: {sent_ok}\n'
        f'Ошибок: {sent_fail}'
        f'{details_text}'
    )


@router.callback_query(F.data == 'admin:set_price')
async def admin_set_price(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.setting_price)
    await callback.message.answer('Формат: model_key option_key price_credits. Пример: nano_banana base 5')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.setting_price)
async def admin_set_price_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    if len(parts) != 3:
        await message.answer('Неверный формат. Пример: nano_banana base 5')
        return
    model_key, option_key, price = parts[0], parts[1], int(parts[2])
    model = get_model(model_key)
    if not model:
        await message.answer('Модель не найдена.')
        return
    service = PricingService(session)
    await service.set_price(model_key, option_key, price, model.model_type, model.provider)
    await session.commit()
    await state.clear()
    await message.answer('Цена обновлена.')


@router.callback_query(F.data == 'admin:bulk')
async def admin_bulk(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.bulk_multiplier)
    await callback.message.answer('Введите множитель, например 1.1 или 0.9')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.bulk_multiplier)
async def admin_bulk_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        multiplier = float(message.text or '1')
    except ValueError:
        await message.answer('Неверный формат.')
        return
    service = PricingService(session)
    count = await service.bulk_multiply(multiplier)
    await session.commit()
    await state.clear()
    await message.answer(f'Обновлено цен: {count}')


@router.callback_query(F.data == 'admin:ref:create')
async def admin_ref_create(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.create_referral)
    await callback.message.answer('Введите скидку в процентах (например 10)')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.create_referral)
async def admin_ref_create_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        pct = int(message.text or '0')
    except ValueError:
        await message.answer('Неверный формат.')
        return
    service = ReferralService(session)
    ref = await service.create_code(pct, message.from_user.id)
    await session.commit()
    await state.clear()
    await message.answer(f'Создан код: {ref.code} со скидкой {pct}%')


@router.callback_query(F.data == 'admin:ref:list')
async def admin_ref_list(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    service = ReferralService(session)
    codes = await service.list_codes()
    if not codes:
        await callback.message.answer('Кодов нет.')
        await callback.answer()
        await safe_cleanup_callback(callback)
        return
    text = '\n'.join([f'{c} - {pct}% - использований: {cnt} - {"активен" if act else "неактивен"}' for c, pct, cnt, act in codes])
    await callback.message.answer(text)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'admin:promo:create')
async def admin_promo_create(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.create_promo)
    await callback.message.answer('Формат: количество credits. Пример: 50 20')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.create_promo)
async def admin_promo_create_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    if len(parts) != 2:
        await message.answer('Неверный формат. Пример: 50 20')
        return
    amount = int(parts[0])
    credits = int(parts[1])
    batch_id = str(uuid.uuid4())
    service = PromoService(session)
    codes = await service.create_batch(amount, credits, message.from_user.id, batch_id)
    await session.commit()

    content = '\n'.join([c.code for c in codes])
    with tempfile.NamedTemporaryFile('w+', delete=False, suffix='.txt') as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    await message.answer_document(open(tmp_path, 'rb'), caption=f'Промо-партия {batch_id} создана.')
    await state.clear()


@router.callback_query(F.data == 'admin:stats')
async def admin_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    service = AnalyticsService(session)
    today = await service.dashboard(1)
    week = await service.dashboard(7)
    month = await service.dashboard(30)

    def fmt(label: str, data: dict) -> str:
        return (
            f'<b>{label}</b>\n'
            f'Выручка (звезды): {data["revenue_stars"]}\n'
            f'Кредитов выдано: {data["credits_issued"]}\n'
            f'Кредитов потрачено: {data["credits_spent"]}\n'
            f'Активные пользователи: {data["active_users"]}\n'
            f'DAU/WAU: {data["dau"]}/{data["wau"]}\n'
            f'Конверсия: {data["conversion_pct"]:.1f}%\n'
            f'Ошибки: {data["failure_rate_pct"]:.1f}%\n'
            f'Средняя задержка: {data["avg_latency_sec"]:.1f}s\n'
        )

    await callback.message.answer(fmt('Сегодня', today))
    await callback.message.answer(fmt('7 дней', week))
    await callback.message.answer(fmt('30 дней', month))
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'admin:grant')
async def admin_grant(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.grant_credits)
    await callback.message.answer('Формат: telegram_id credits (пример: 123456 50)')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.grant_credits)
async def admin_grant_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    if len(parts) != 2:
        await message.answer('Неверный формат.')
        return
    telegram_id = int(parts[0])
    credits = int(parts[1])
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer('Пользователь не найден.')
        return
    credits_service = CreditsService(session)
    await credits_service.add_ledger(user, credits, 'admin_grant', meta={'admin': message.from_user.id})
    await session.commit()
    await state.clear()
    await message.answer('Кредиты выданы.')


@router.callback_query(F.data == 'admin:ban')
async def admin_ban(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await state.set_state(AdminFlow.ban_user)
    await callback.message.answer('Формат: telegram_id on/off (пример: 123456 on)')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.ban_user)
async def admin_ban_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    parts = (message.text or '').split()
    if len(parts) != 2:
        await message.answer('Неверный формат.')
        return
    telegram_id = int(parts[0])
    flag = parts[1].lower() == 'on'
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer('Пользователь не найден.')
        return
    user.is_banned = flag
    await session.commit()
    await state.clear()
    await message.answer('Статус обновлен.')


@router.callback_query(F.data == 'admin:free_mode')
async def admin_free_mode(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user:
        await callback.answer('Пользователь не найден', show_alert=True)
        return
    current = bool(user.settings.get('admin_free_mode', get_settings().admin_free_mode_default))
    user.settings['admin_free_mode'] = not current
    await session.commit()
    await callback.message.answer(f'Admin free-mode теперь: {"ON" if not current else "OFF"}')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data.startswith('support:templates:'))
async def support_templates(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await callback.message.answer('Шаблоны пока не настроены.')
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.callback_query(F.data == 'support:reply_cancel')
async def support_reply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get('support_prompt_id')
    await state.clear()
    if prompt_id:
        try:
            await callback.message.bot.delete_message(callback.message.chat.id, prompt_id)
        except Exception:
            pass
    await callback.answer('Отменено', show_alert=False)


@router.callback_query(F.data.startswith('support:reply:'))
async def support_reply(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    parts = (callback.data or '').split(':')
    if len(parts) != 3:
        await callback.answer('Некорректные данные', show_alert=True)
        return
    thread_id = int(parts[2])
    await state.set_state(AdminFlow.support_reply)
    prompt = await callback.message.answer(
        'Напишите ответ одним сообщением.',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text='Отмена', callback_data='support:reply_cancel')]]
        ),
    )
    await state.update_data(support_thread_id=thread_id, support_prompt_id=prompt.message_id)
    await callback.answer()
    await safe_cleanup_callback(callback)


@router.message(AdminFlow.support_reply)
async def support_reply_send(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    thread_id = data.get('support_thread_id')
    prompt_id = data.get('support_prompt_id')
    if not thread_id:
        await message.answer('Чат не найден.')
        await state.clear()
        return

    settings = get_settings()

    support = SupportService(session)
    thread = await support.get_thread(int(thread_id))
    if not thread:
        await message.answer('Чат не найден.')
        await state.clear()
        return

    result = await session.execute(select(User).where(User.id == thread.user_id))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer('Пользователь не найден.')
        await state.clear()
        return

    if settings.support_bot_token:
        bot = Bot(
            token=settings.support_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            sent = await bot.send_message(user.telegram_id, message.text or '')
        finally:
            await bot.session.close()
    else:
        sent = await message.bot.send_message(user.telegram_id, message.text or '')

    await support.add_message(thread, 'admin', message.text or '', sender_admin_id=message.from_user.id, tg_message_id=sent.message_id)
    await session.commit()

    await state.clear()
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    await message.answer('Ответ отправлен.')
