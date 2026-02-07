from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.modelspecs.base import ModelSpec, OptionSpec


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='🎨 Сгенерировать', callback_data='gen:start')],
            [InlineKeyboardButton(text='💳 Купить кредиты', callback_data='pay:buy')],
            [InlineKeyboardButton(text='🧮 Арифметика расхода', callback_data='prices:list')],
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
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for model in models:
        label = _model_label(model)
        row.append(InlineKeyboardButton(text=label, callback_data=f'gen:model:{model.key}'))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_label(model: ModelSpec) -> str:
    icon_map = {
        'nano_banana': '🍌',
        'nano_banana_pro': '⭐',
        'nano_banana_edit': '🛠️',
    }
    icon = icon_map.get(model.key, '✨')
    return f'{icon} {model.display_name}'


def ref_mode_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='🚫 Без референсов', callback_data='gen:refmode:none')],
            [InlineKeyboardButton(text='📎 С референсами', callback_data='gen:refmode:has')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:back')],
        ]
    )


def option_menu(option: OptionSpec, selected: str) -> InlineKeyboardMarkup:
    rows = []
    for val in option.values:
        marker = '[x] ' if val.value == selected else ''
        rows.append([InlineKeyboardButton(text=f'{marker}{val.label}', callback_data=f'gen:opt:{option.key}:{val.value}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='gen:options:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def options_panel(
    model: ModelSpec,
    options: dict[str, str],
    outputs: int,
    max_outputs: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    emoji_map = {
        'output_format': '🖼️',
        'image_size': '📐',
        'aspect_ratio': '📐',
        'resolution': '🧩',
        'reference_images': '📎',
    }
    for opt in model.options:
        if opt.ui_hidden:
            continue
        emoji = emoji_map.get(opt.key, '⚙️')
        rows.append([InlineKeyboardButton(text=f'— {emoji} {opt.label} —', callback_data='gen:noop')])
        line: list[InlineKeyboardButton] = []
        selected = options.get(opt.key, opt.default)
        for value in opt.values:
            marker = '✅ ' if value.value == selected else ''
            line.append(
                InlineKeyboardButton(
                    text=f'{marker}{value.label}',
                    callback_data=f'gen:opt:{opt.key}:{value.value}',
                )
            )
            if len(line) == 2:
                rows.append(line)
                line = []
        if line:
            rows.append(line)

    rows.append([InlineKeyboardButton(text='— 🔢 Количество —', callback_data='gen:noop')])
    line = []
    for i in range(1, max_outputs + 1):
        marker = '✅ ' if i == outputs else ''
        line.append(InlineKeyboardButton(text=f'{marker}{i}', callback_data=f'gen:outputs:{i}'))
        if len(line) == 4:
            rows.append(line)
            line = []
    if line:
        rows.append(line)

    rows.append([InlineKeyboardButton(text='➡️ Далее', callback_data='gen:options:next')])
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


def generation_result_menu(generation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='🆕 Начать заново', callback_data='gen:result:restart'),
                InlineKeyboardButton(text='🔁 Повторить', callback_data=f'gen:result:repeat:{generation_id}'),
            ],
            [InlineKeyboardButton(text='❌ Завершить', callback_data='gen:result:finish')],
        ]
    )


def repeat_confirm_menu(generation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✅ Отправить', callback_data=f'gen:repeat:confirm:{generation_id}')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='gen:repeat:cancel')],
        ]
    )
