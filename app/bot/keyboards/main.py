from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.modelspecs.base import ModelSpec, OptionSpec


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='🎨 Сгенерировать', callback_data='gen:start')],
            [InlineKeyboardButton(text='💳 Купить кредиты', callback_data='pay:buy')],
            [InlineKeyboardButton(text='🕘 История', callback_data='history:list')],
            [InlineKeyboardButton(text='ℹ️ Помощь', callback_data='help')],
        ]
    )


def generate_category_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='🖼️ Изображения', callback_data='gen:category:image')],
            [InlineKeyboardButton(text='🎬 Видео (скоро)', callback_data='gen:category:video')],
        ]
    )


def model_menu(models: list[ModelSpec]) -> InlineKeyboardMarkup:
    buttons = []
    for model in models:
        buttons.append([InlineKeyboardButton(text=model.display_name, callback_data=f'gen:model:{model.key}')])
    buttons.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:back')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def option_menu(option: OptionSpec, selected: str) -> InlineKeyboardMarkup:
    rows = []
    for val in option.values:
        marker = '[x] ' if val.value == selected else ''
        rows.append([InlineKeyboardButton(text=f'{marker}{val.label}', callback_data=f'gen:opt:{option.key}:{val.value}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:options:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def outputs_menu(max_outputs: int, selected: int) -> InlineKeyboardMarkup:
    rows = []
    for i in range(1, max_outputs + 1):
        marker = '[x] ' if i == selected else ''
        rows.append([InlineKeyboardButton(text=f'{marker}{i} шт.', callback_data=f'gen:outputs:{i}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:outputs:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✅ Подтвердить', callback_data='gen:confirm')],
            [InlineKeyboardButton(text='✏️ Изменить промпт', callback_data='gen:edit:prompt')],
            [InlineKeyboardButton(text='⚙️ Изменить опции', callback_data='gen:edit:options')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='gen:cancel')],
        ]
    )
