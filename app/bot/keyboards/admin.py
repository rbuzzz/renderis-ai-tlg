from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Установить цену', callback_data='admin:set_price')],
            [InlineKeyboardButton(text='Множитель цен', callback_data='admin:bulk')],
            [InlineKeyboardButton(text='Создать реф. код', callback_data='admin:ref:create')],
            [InlineKeyboardButton(text='Реф. коды', callback_data='admin:ref:list')],
            [InlineKeyboardButton(text='Промо-партия', callback_data='admin:promo:create')],
            [InlineKeyboardButton(text='Статистика', callback_data='admin:stats')],
            [InlineKeyboardButton(text='Выдать кредиты', callback_data='admin:grant')],
            [InlineKeyboardButton(text='Бан/разбан', callback_data='admin:ban')],
            [InlineKeyboardButton(text='Admin free-mode', callback_data='admin:free_mode')],
        ]
    )
