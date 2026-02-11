from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import t

from app.modelspecs.base import ModelSpec, OptionSpec


def main_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "menu_generate"), callback_data='gen:start')],
            [InlineKeyboardButton(text=t(lang, "menu_buy"), callback_data='pay:buy')],
            [InlineKeyboardButton(text=t(lang, "menu_prices"), callback_data='prices:list')],
            [InlineKeyboardButton(text=t(lang, "menu_history"), callback_data='history:list')],
            [InlineKeyboardButton(text=t(lang, "menu_settings"), callback_data='settings:open')],
            [InlineKeyboardButton(text=t(lang, "menu_help"), callback_data='help')],
        ]
    )


def topup_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "payment_topup_promo"), callback_data='pay:topup:promo')],
            [InlineKeyboardButton(text=t(lang, "payment_topup_stars"), callback_data='pay:topup:stars')],
            [InlineKeyboardButton(text=t(lang, "payment_topup_wallet"), callback_data='pay:topup:wallet')],
        ]
    )


def promo_input_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "payment_promo_cancel"), callback_data='pay:topup:promo:cancel')],
        ]
    )


def settings_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "settings_language"), callback_data='settings:language')],
            [InlineKeyboardButton(text=t(lang, "settings_back"), callback_data='settings:back')],
        ]
    )


def language_menu(current_lang: str, lang: str = "ru", include_back: bool = True) -> InlineKeyboardMarkup:
    labels = [
        ("en", "🇬🇧 English"),
        ("es", "🇪🇸 Espanol"),
        ("ru", "🇷🇺 Русский"),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for code, title in labels:
        prefix = "✅ " if code == current_lang else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"settings:lang:{code}")])
    if include_back:
        rows.append([InlineKeyboardButton(text=t(lang, "settings_back"), callback_data='settings:open')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def generate_category_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "category_images"), callback_data='gen:category:image')],
            [InlineKeyboardButton(text=t(lang, "category_video"), callback_data='gen:category:video')],
        ]
    )


def model_menu(models: list[ModelSpec], lang: str = "ru") -> InlineKeyboardMarkup:
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
    rows.append([InlineKeyboardButton(text=t(lang, "ref_back_btn"), callback_data='gen:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_label(model: ModelSpec) -> str:
    icon_map = {
        'nano_banana': '🍌',
        'nano_banana_pro': '⭐',
        'nano_banana_edit': '🛠️',
    }
    icon = icon_map.get(model.key, '✨')
    return f'{icon} {model.display_name}'


def ref_mode_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "ref_mode_none"), callback_data='gen:refmode:none')],
            [InlineKeyboardButton(text=t(lang, "ref_mode_has"), callback_data='gen:refmode:has')],
            [InlineKeyboardButton(text=t(lang, "ref_back_btn"), callback_data='gen:back')],
        ]
    )


def option_menu(option: OptionSpec, selected: str, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for val in option.values:
        marker = '[x] ' if val.value == selected else ''
        label = _value_label(option.key, val.value, val.label, lang)
        rows.append([InlineKeyboardButton(text=f'{marker}{label}', callback_data=f'gen:opt:{option.key}:{val.value}')])
    rows.append([InlineKeyboardButton(text=t(lang, "options_back"), callback_data='gen:options:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def options_panel(
    model: ModelSpec,
    options: dict[str, str],
    outputs: int,
    max_outputs: int,
    lang: str = "ru",
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
        opt_label = _option_label(opt.key, opt.label, lang)
        rows.append([InlineKeyboardButton(text=f'— {emoji} {opt_label} —', callback_data='gen:noop')])
        line: list[InlineKeyboardButton] = []
        selected = options.get(opt.key, opt.default)
        for value in opt.values:
            marker = '✅ ' if value.value == selected else ''
            label = _value_label(opt.key, value.value, value.label, lang)
            line.append(
                InlineKeyboardButton(
                    text=f'{marker}{label}',
                    callback_data=f'gen:opt:{opt.key}:{value.value}',
                )
            )
            if len(line) == 2:
                rows.append(line)
                line = []
        if line:
            rows.append(line)

    rows.append([InlineKeyboardButton(text=t(lang, "options_count"), callback_data='gen:noop')])
    line = []
    for i in range(1, max_outputs + 1):
        marker = '✅ ' if i == outputs else ''
        line.append(InlineKeyboardButton(text=f'{marker}{i}', callback_data=f'gen:outputs:{i}'))
        if len(line) == 4:
            rows.append(line)
            line = []
    if line:
        rows.append(line)

    rows.append([InlineKeyboardButton(text=t(lang, "options_next"), callback_data='gen:options:next')])
    rows.append([InlineKeyboardButton(text=t(lang, "options_back"), callback_data='gen:options:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def outputs_menu(max_outputs: int, selected: int, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for i in range(1, max_outputs + 1):
        marker = '[x] ' if i == selected else ''
        rows.append([InlineKeyboardButton(text=f'{marker}{i} шт.', callback_data=f'gen:outputs:{i}')])
    rows.append([InlineKeyboardButton(text=t(lang, "options_back"), callback_data='gen:outputs:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "confirm_yes"), callback_data='gen:confirm')],
            [InlineKeyboardButton(text=t(lang, "confirm_edit_prompt"), callback_data='gen:edit:prompt')],
            [InlineKeyboardButton(text=t(lang, "confirm_edit_options"), callback_data='gen:edit:options')],
            [InlineKeyboardButton(text=t(lang, "confirm_cancel"), callback_data='gen:cancel')],
        ]
    )


def generation_result_menu(generation_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t(lang, "result_restart"), callback_data='gen:result:restart'),
                InlineKeyboardButton(text=t(lang, "result_repeat"), callback_data=f'gen:result:repeat:{generation_id}'),
            ],
            [InlineKeyboardButton(text=t(lang, "result_finish"), callback_data='gen:result:finish')],
        ]
    )


def repeat_confirm_menu(generation_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "repeat_send"), callback_data=f'gen:repeat:confirm:{generation_id}')],
            [InlineKeyboardButton(text=t(lang, "repeat_cancel"), callback_data='gen:repeat:cancel')],
        ]
    )


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
