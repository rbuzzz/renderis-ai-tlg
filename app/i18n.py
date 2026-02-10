from __future__ import annotations

from typing import Dict


SUPPORTED_LANGS = ("en", "es", "ru")


def normalize_lang(code: str | None) -> str:
    if not code:
        return "en"
    value = code.lower()
    if value.startswith("ru"):
        return "ru"
    if value.startswith("en"):
        return "en"
    if value.startswith("es"):
        return "es"
    return "en"


BASE_RU: Dict[str, str] = {
    # Site
    "site_title": "Renderis Studio",
    "site_tagline": "Генерация изображений прямо в браузере",
    "site_notice": (
        "Результаты на сайте хранятся не более 15 дней. "
        "Все генерации также дублируются в Telegram-боте Renderis Studio."
    ),
    "input_title": "Панель генерации",
    "output_title": "Результат",
    "output_empty": "Пока нет результатов. Запустите генерацию.",
    "download": "Скачать",
    "delete": "Удалить",
    "history_title": "История",
    "balance": "Баланс",
    "credits": "кредитов",
    "topup_button": "Пополнить",
    "topup_redeem_option": "Redeem promo code",
    "topup_stars_option": "Telegram Stars",
    "topup_crypto_option": "Crypto",
    "topup_modal_redeem_title": "Активация промо-кода",
    "topup_modal_crypto_title": "Крипто-оплата",
    "topup_stars_redirect": "Открываем Telegram для оплаты Stars...",
    "topup_stars_unavailable": "Telegram-бот не настроен.",
    "language": "Язык",
    "redeem": "Промо-код",
    "redeem_placeholder": "Введите промо-код",
    "redeem_button": "Активировать",
    "model_label": "Модель",
    "model_tagline_nano_banana": "Быстрые генерации по вашему описанию.",
    "model_tagline_nano_banana_pro": "Больше деталей и качество. Можно добавлять референсы.",
    "model_tagline_nano_banana_edit": "Редактирование по вашим фотографиям.",
    "prompt_label": "Промпт",
    "prompt_placeholder": "Опишите, что нужно создать...",
    "upload_label": "Референсы",
    "upload_hint": "Можно загрузить до 8 изображений (Edit — до 10).",
    "upload_hint_required": "Нужно добавить минимум 1 изображение (до {max}).",
    "upload_hint_optional": "Можно добавить до {max} изображений.",
    "upload_required": "Добавьте хотя бы одно изображение.",
    "upload_count": "Выбрано {count} из {max} файлов.",
    "upload_button": "Добавить файлы",
    "ref_images_title": "Референс-изображения {count}/{max}",
    "ref_images_note": "Можно выбрать до {max} изображений для объединения",
    "ref_add": "Добавить фото",
    "ref_replace": "Заменить",
    "ref_add_sub": "Можно несколько, до 50MB",
    "options_label": "Параметры",
    "aspect_ratio": "Соотношение сторон",
    "resolution": "Разрешение",
    "output_format": "Формат",
    "outputs": "Количество",
    "run": "Сгенерировать",
    "run_pending": "Отправляем запрос...",
    "history": "История",
    "history_empty": "Пока нет генераций.",
    "history_deleted": "Файл удалён",
    "logout": "Выйти",
    "login_title": "Вход в Renderis",
    "login_subtitle": "Авторизуйтесь через Telegram, чтобы видеть баланс и историю.",
    "login_failed": "Не удалось авторизоваться.",
    "login_required": "Войдите через Telegram, чтобы продолжить.",
    "prompt_required": "Промпт не может быть пустым.",
    "request_sent": "Запрос отправлен.",
    "error_prefix": "Ошибка",
    "promo_added": "Начислено",
    "promo_error": "Ошибка",
    "crypto_title": "Крипто-оплата",
    "crypto_select_package": "Выберите пакет",
    "crypto_create_invoice": "Создать счёт",
    "crypto_open_invoice": "Открыть оплату",
    "crypto_check_status": "Проверить оплату",
    "crypto_loading_packages": "Загружаем пакеты...",
    "crypto_packages_empty": "Пакеты пока не настроены.",
    "crypto_invoice_created": "Счёт создан на {amount} {currency}. Оплатите и проверьте статус.",
    "crypto_waiting_payment": "Ожидаем подтверждение оплаты...",
    "crypto_partial_payment": "Обнаружена частичная оплата. Доплатите счёт.",
    "crypto_paid": "Оплата подтверждена. Начислено {credits} кредитов.",
    "crypto_canceled": "Счёт отменён или просрочен.",
    "crypto_unavailable": "Крипто-оплата сейчас недоступна.",
    "crypto_create_failed": "Не удалось создать счёт.",
    "crypto_status_failed": "Не удалось проверить статус оплаты.",
    "crypto_best_value": "Лучшая цена",
    "crypto_bonus_badge": "Бонус +{bonus}",
    "crypto_save_badge": "Экономия {pct}%",
    "crypto_base_bonus_line": "База {base} + бонус {bonus}",
    "crypto_total_line": "Итого {credits} кредитов",
    "delete_failed": "Не удалось удалить",
    "quote_line": "Будет списано: {total} кр.",
    "quote_login_required": "Войдите, чтобы увидеть стоимость.",
    "quote_unavailable": "Не удалось рассчитать стоимость.",
    # Ratios / resolutions
    "ratio_1_1": "1:1 (квадрат)",
    "ratio_2_3": "2:3 (портрет)",
    "ratio_3_4": "3:4 (портрет)",
    "ratio_3_2": "3:2 (ландшафт)",
    "ratio_4_3": "4:3 (ландшафт)",
    "ratio_4_5": "4:5 (портрет)",
    "ratio_5_4": "5:4 (ландшафт)",
    "ratio_9_16": "9:16 (портрет)",
    "ratio_16_9": "16:9 (ландшафт)",
    "ratio_21_9": "21:9 (киношный)",
    "ratio_auto": "Auto",
    "res_1k": "1K",
    "res_2k": "2K",
    "res_4k": "4K",
    # Bot menu
    "menu_generate": "🎨 Сгенерировать",
    "menu_buy": "💳 Пополнить баланс",
    "menu_prices": "🧮 Арифметика расхода",
    "menu_history": "🕘 История",
    "menu_help": "ℹ️ Помощь",
    "category_choose": "📂 Выберите категорию:",
    "category_images": "🖼️ Изображения",
    "category_video": "🎬 Видео (скоро)",
    "video_soon": "🎬 Видео пока недоступно. Скоро добавим.",
    "model_intro_title": "🖼️ <b>Gemini Images</b>",
    "model_intro_desc": "Создавайте и редактируйте изображения прямо в чате.",
    "model_intro_models": (
        "Для вас работают "
        "{count, plural, one {# модель} few {# модели} many {# моделей} other {# модели}}:"
    ),
    "model_intro_select": "Выберите модель ниже:",
    "model_not_found": "Модель не найдена",
    "prompt_enter": "✍️ Введите промпт для {model}:",
    "prompt_enter_new": "✍️ Введите новый промпт:",
    "prompt_empty_bot": "Промпт не может быть пустым. Попробуйте еще раз.",
    "prompt_banned": "Запрос содержит запрещенные слова. Измените промпт.",
    "ref_optional": "📎 Можно добавить референсы для более точного результата.\nХотите использовать референсы?",
    "ref_limit": "Достигнут лимит референс-изображений.",
    "ref_required": "Нужно добавить хотя бы одно фото.",
    "ref_required_mode": "Для этого режима нужны референсы.",
    "ref_send_photo": "Пожалуйста, отправьте фото или нажмите «Готово».",
    "ref_done_btn": "✅ Готово",
    "ref_skip_btn": "⏭️ Пропустить",
    "ref_back_btn": "⬅️ Назад",
    "ref_mode_none": "🚫 Без референсов",
    "ref_mode_has": "📎 С референсами",
    "ref_label_photo": "фото",
    "ref_label_ref": "референс-фото",
    "ref_prompt_initial": "📎 Отправьте до {max} {label}.\nКогда закончите, нажмите «Готово».",
    "ref_prompt_more": "✅ Добавлено {count} из {max} фото.\nПришлите еще {label} или нажмите «Готово».",
    "ref_prompt_done": "✅ Добавлено {count} из {max} фото.\nНажмите «Готово».",
    "options_title": "⚙️ <b>Параметры генерации</b>",
    "options_model": "🧠 Модель: {model}",
    "options_prompt": "✍️ Промпт: {prompt}",
    "options_refs": (
        "📎 "
        "{count, plural, one {# референс} few {# референса} many {# референсов} other {# референса}}"
    ),
    "options_instruction": "Отметьте нужные параметры и нажмите «Далее».",
    "options_next": "➡️ Далее",
    "options_back": "⬅️ Назад",
    "options_count": "— 🔢 Количество —",
    "preview_title": "✅ <b>Проверьте стоимость</b>",
    "preview_model": "🧠 Модель: {model}",
    "preview_prompt": "✍️ Промпт: {prompt}",
    "preview_refs": (
        "📎 "
        "{count, plural, one {# референс} few {# референса} many {# референсов} other {# референса}}"
    ),
    "preview_outputs": (
        "🔢 "
        "{count, plural, one {# выход} few {# выхода} many {# выходов} other {# выхода}}"
    ),
    "preview_cost_per": "💳 Цена за 1: {cost} кр.",
    "preview_total": "🧾 Итого: {total} кр.",
    "preview_discount": "Скидка: {pct}%",
    "preview_notice": "Подтверждая, вы соглашаетесь соблюдать закон и правила сервиса.",
    "confirm_yes": "✅ Подтвердить",
    "confirm_edit_prompt": "✏️ Изменить промпт",
    "confirm_edit_options": "⚙️ Изменить опции",
    "confirm_cancel": "❌ Отмена",
    "cancelled": "❌ Отменено.",
    "task_started": "Задача запущена. Как только будет готово — отправлю результат.",
    "queued": "Сервис перегружен, задача поставлена в очередь. Примерная позиция: {pos}.",
    "api_auth_error": "Ошибка доступа к API. Проверьте ключ Kie.ai.",
    "create_failed": "Не удалось создать задачу. Попробуйте позже.",
    "result_next": "Что дальше?",
    "result_restart": "🆕 Начать заново",
    "result_repeat": "🔁 Повторить",
    "result_finish": "❌ Завершить",
    "result_done": "✅ Завершено. Если хотите еще — нажмите /start.",
    "repeat_prompt": (
        "Отправить запрос повторно?\n"
        "Будет списано {cost} "
        "{cost, plural, one {кредит} few {кредита} many {кредитов} other {кредита}}."
    ),
    "repeat_send": "✅ Отправить",
    "repeat_cancel": "❌ Отмена",
    "repeat_cancelled": "Отменено.",
    "error_banned": "Доступ запрещен.",
    "error_outputs": "Недопустимое число вариантов.",
    "error_too_many": "Слишком много активных задач. Подождите завершения текущих.",
    "error_daily_cap": "Достигнут дневной лимит расходов.",
    "error_no_credits": "Недостаточно кредитов. Купите пакет.",
    "error_refs_required": "Для этого режима нужно добавить хотя бы одно фото.",
    "error_generic": "Не удалось запустить генерацию.",
    "help_text": "ℹ️ Это бот для генерации изображений. Используйте меню ниже.\nКоманды: /start /ref CODE /promo CODE /admin (для админов).",
    # Start
    "start_hello": "👋 Привет, {name}!",
    "start_balance": (
        "💰 Баланс: <b>{credits}</b> "
        "{credits, plural, one {кредит} few {кредита} many {кредитов} other {кредита}}."
    ),
    "start_terms": "📜 Используя бот, вы подтверждаете соблюдение законов и правил сервиса.",
    "start_bonus": (
        "Бонус за старт: +{credits} "
        "{credits, plural, one {кредит} few {кредита} many {кредитов} other {кредита}}."
    ),
    # Prices list
    "prices_title": "🧮 <b>Арифметика расхода кредитов</b>",
    "prices_note": "Цены актуальны на момент запроса и меняются мгновенно после правок администратора.",
    "prices_nb": "🍌 Nano Banana — <b>{cost}</b> кр.",
    "prices_edit": "🛠️ Nano Banana Edit — <b>{cost}</b> кр.",
    "prices_pro_no_refs_1k": "⭐ Pro без референсов 1K — <b>{cost}</b> кр.",
    "prices_pro_no_refs_2k": "⭐ Pro без референсов 2K — <b>{cost}</b> кр.",
    "prices_pro_no_refs_4k": "⭐ Pro без референсов 4K — <b>{cost}</b> кр.",
    "prices_pro_refs_1k": "📎 Pro с референсами 1K — <b>{cost}</b> кр.",
    "prices_pro_refs_2k": "📎 Pro с референсами 2K — <b>{cost}</b> кр.",
    "prices_pro_refs_4k": "📎 Pro с референсами 4K — <b>{cost}</b> кр.",
    # History
    "history_user_not_found": "Пользователь не найден.",
    "history_empty_bot": "🕘 История пуста.",
    "history_not_found": "Не найдено",
    "history_no_access": "Недостаточно прав.",
    "history_not_ready": "⏳ Результаты еще не готовы.",
    "history_links_missing": "⚠️ Ссылки не найдены.",
    "history_open_results": "Открыть результаты",
    "history_regen": "Регенерировать",
    "history_regen_started": "✅ Регенерация запущена.",
    "history_created": "Создано",
    # Payments
    "payment_packages_missing": "⚠️ Пакеты не настроены. Обратитесь к администратору.",
    "payment_topup_choose": "💳 Выберите способ пополнения:",
    "payment_topup_promo": "🎟️ Ввести промо-код",
    "payment_topup_stars": "⭐ Купить за звезды",
    "payment_promo_enter": "Отправьте промо-код без дополнительных знаков.",
    "payment_promo_cancel": "❌ Отмена",
    "payment_promo_cancelled": "Ввод промо-кода отменен.",
    "payment_choose": "💳 Выберите пакет:",
    "payment_package_not_found": "Пакет не найден",
    "payment_invalid": "Некорректный платеж.",
    "payment_processed": "Платеж уже обработан.",
    "payment_success": (
        "Оплата принята. Начислено {credits} "
        "{credits, plural, one {кредит} few {кредита} many {кредитов} other {кредита}}."
    ),
    "payment_user_not_found": "Пользователь не найден.",
    "payment_desc": (
        "{credits} {credits, plural, one {кредит} few {кредита} many {кредитов} other {кредита}}"
    ),
    # Referral / Promo
    "ref_usage": "Использование: /ref CODE",
    "ref_not_found": "Код не найден или неактивен.",
    "ref_already": "Реферальный код уже применён ранее.",
    "ref_applied": "Реферальный код применён. Скидка будет учтена в генерациях.",
    "promo_usage": "Использование: /promo CODE",
    "promo_invalid": "Промо-код не найден или неактивен.",
    "promo_used": "Промо-код уже использован.",
    "promo_not_found": "Промо-код не найден.",
    "promo_activated": (
        "Промо-код активирован. Начислено {credits} "
        "{credits, plural, one {кредит} few {кредита} many {кредитов} other {кредита}}."
    ),
    # Poller / results
    "result_no_urls": "Генерация завершена, но ссылки не получены.",
    "result_original": "Изображение без сжатия",
    "result_caption": "Промпт: {prompt}\nНапишите в чат, если нужно изменить что-то еще.",
    "result_send_failed": "Не удалось отправить результат. Попробуйте позже.",
    "generation_failed": "Генерация не удалась.",
    "generation_failed_reason": "Генерация не удалась. Причина: {reason}",
    "report_thanks": "Спасибо за сообщение! Мы проверим результат.",
}


BASE_EN: Dict[str, str] = {
    "site_title": "Renderis Studio",
    "site_tagline": "Generate images right in your browser",
    "site_notice": "Site results are stored for no more than 15 days. All generations are also delivered in the Renderis Studio Telegram bot.",
    "input_title": "Generation panel",
    "output_title": "Result",
    "output_empty": "No results yet. Start a generation.",
    "download": "Download",
    "delete": "Delete",
    "history_title": "History",
    "balance": "Balance",
    "credits": "credits",
    "topup_button": "Top Up",
    "topup_redeem_option": "Redeem promo code",
    "topup_stars_option": "Telegram Stars",
    "topup_crypto_option": "Crypto",
    "topup_modal_redeem_title": "Redeem promo code",
    "topup_modal_crypto_title": "Crypto payment",
    "topup_stars_redirect": "Opening Telegram for Stars payment...",
    "topup_stars_unavailable": "Telegram bot is not configured.",
    "language": "Language",
    "redeem": "Promo code",
    "redeem_placeholder": "Enter promo code",
    "redeem_button": "Redeem",
    "model_label": "Model",
    "model_tagline_nano_banana": "Fast generations from your description.",
    "model_tagline_nano_banana_pro": "More detail and quality. You can add references.",
    "model_tagline_nano_banana_edit": "Editing based on your photos.",
    "prompt_label": "Prompt",
    "prompt_placeholder": "Describe what you want to create...",
    "upload_label": "References",
    "upload_hint": "Upload up to 8 images (Edit: up to 10).",
    "upload_hint_required": "At least 1 image required (up to {max}).",
    "upload_hint_optional": "Up to {max} images allowed.",
    "upload_required": "Please add at least one image.",
    "upload_count": "Selected {count} of {max} files.",
    "upload_button": "Add files",
    "ref_images_title": "Reference Images {count}/{max}",
    "ref_images_note": "Pro users can select up to {max} images to merge into one",
    "ref_add": "Add image",
    "ref_replace": "Replace",
    "ref_add_sub": "Multiple files, up to 50MB each",
    "options_label": "Options",
    "aspect_ratio": "Aspect ratio",
    "resolution": "Resolution",
    "output_format": "Format",
    "outputs": "Outputs",
    "run": "Run",
    "run_pending": "Sending request...",
    "history": "History",
    "history_empty": "No generations yet.",
    "history_deleted": "File deleted",
    "logout": "Log out",
    "login_title": "Sign in to Renderis",
    "login_subtitle": "Use Telegram login to see your balance and history.",
    "login_failed": "Authorization failed.",
    "login_required": "Please login via Telegram to continue.",
    "prompt_required": "Prompt cannot be empty.",
    "request_sent": "Request sent.",
    "error_prefix": "Error",
    "promo_added": "Added",
    "promo_error": "Error",
    "crypto_title": "Crypto payment",
    "crypto_select_package": "Choose a package",
    "crypto_create_invoice": "Create invoice",
    "crypto_open_invoice": "Open payment",
    "crypto_check_status": "Check payment",
    "crypto_loading_packages": "Loading packages...",
    "crypto_packages_empty": "No packages configured yet.",
    "crypto_invoice_created": "Invoice created for {amount} {currency}. Complete payment and check status.",
    "crypto_waiting_payment": "Waiting for payment confirmation...",
    "crypto_partial_payment": "Partial payment detected. Please complete the invoice.",
    "crypto_paid": "Payment confirmed. Credited {credits} credits.",
    "crypto_canceled": "Invoice canceled or expired.",
    "crypto_unavailable": "Crypto payment is unavailable right now.",
    "crypto_create_failed": "Failed to create invoice.",
    "crypto_status_failed": "Failed to check payment status.",
    "crypto_best_value": "Best value",
    "crypto_bonus_badge": "+{bonus} bonus",
    "crypto_save_badge": "Save {pct}%",
    "crypto_base_bonus_line": "Base {base} + bonus {bonus}",
    "crypto_total_line": "Total {credits} credits",
    "delete_failed": "Delete failed",
    "quote_line": "Cost: {total} credits",
    "quote_login_required": "Login to see the cost.",
    "quote_unavailable": "Unable to calculate the cost.",
    "ratio_1_1": "1:1 (square)",
    "ratio_2_3": "2:3 (portrait)",
    "ratio_3_4": "3:4 (portrait)",
    "ratio_3_2": "3:2 (landscape)",
    "ratio_4_3": "4:3 (landscape)",
    "ratio_4_5": "4:5 (portrait)",
    "ratio_5_4": "5:4 (landscape)",
    "ratio_9_16": "9:16 (portrait)",
    "ratio_16_9": "16:9 (landscape)",
    "ratio_21_9": "21:9 (cinematic)",
    "ratio_auto": "Auto",
    "res_1k": "1K",
    "res_2k": "2K",
    "res_4k": "4K",
    "menu_generate": "🎨 Generate",
    "menu_buy": "💳 Top up balance",
    "menu_prices": "🧮 Credit math",
    "menu_history": "🕘 History",
    "menu_help": "ℹ️ Help",
    "category_choose": "📂 Choose category:",
    "category_images": "🖼️ Images",
    "category_video": "🎬 Video (soon)",
    "video_soon": "🎬 Video is not available yet. Coming soon.",
    "model_intro_title": "🖼️ <b>Gemini Images</b>",
    "model_intro_desc": "Create and edit images right in chat.",
    "model_intro_models": "Available {count, plural, one {# model} other {# models}}:",
    "model_intro_select": "Choose a model below:",
    "model_not_found": "Model not found",
    "prompt_enter": "✍️ Enter prompt for {model}:",
    "prompt_enter_new": "✍️ Enter new prompt:",
    "prompt_empty_bot": "Prompt cannot be empty. Try again.",
    "prompt_banned": "Your prompt contains forbidden words. Please edit it.",
    "ref_optional": "📎 You can add references for better results.\nUse references?",
    "ref_limit": "Reference limit reached.",
    "ref_required": "Please add at least one photo.",
    "ref_required_mode": "References are required for this mode.",
    "ref_send_photo": "Please send a photo or tap “Done”.",
    "ref_done_btn": "✅ Done",
    "ref_skip_btn": "⏭️ Skip",
    "ref_back_btn": "⬅️ Back",
    "ref_mode_none": "🚫 No references",
    "ref_mode_has": "📎 With references",
    "ref_label_photo": "photo",
    "ref_label_ref": "reference photo",
    "ref_prompt_initial": "📎 Send up to {max} {label}.\nWhen finished, tap “Done”.",
    "ref_prompt_more": "✅ Added {count} of {max} photos.\nSend more {label} or tap “Done”.",
    "ref_prompt_done": "✅ Added {count} of {max} photos.\nTap “Done”.",
    "options_title": "⚙️ <b>Generation options</b>",
    "options_model": "🧠 Model: {model}",
    "options_prompt": "✍️ Prompt: {prompt}",
    "options_refs": "{count, plural, one {📎 # reference} other {📎 # references}}",
    "options_instruction": "Select options and tap “Next”.",
    "options_next": "➡️ Next",
    "options_back": "⬅️ Back",
    "options_count": "— 🔢 Quantity —",
    "preview_title": "✅ <b>Check the cost</b>",
    "preview_model": "🧠 Model: {model}",
    "preview_prompt": "✍️ Prompt: {prompt}",
    "preview_refs": "{count, plural, one {📎 # reference} other {📎 # references}}",
    "preview_outputs": "{count, plural, one {🔢 # output} other {🔢 # outputs}}",
    "preview_cost_per": "💳 Cost per 1: {cost} cr.",
    "preview_total": "🧾 Total: {total} cr.",
    "preview_discount": "Discount: {pct}%",
    "preview_notice": "By confirming, you agree to follow the rules and laws.",
    "confirm_yes": "✅ Confirm",
    "confirm_edit_prompt": "✏️ Edit prompt",
    "confirm_edit_options": "⚙️ Edit options",
    "confirm_cancel": "❌ Cancel",
    "cancelled": "❌ Cancelled.",
    "task_started": "Task started. I will send the result when ready.",
    "queued": "Service is busy, task queued. Position: {pos}.",
    "api_auth_error": "API access error. Check Kie.ai key.",
    "create_failed": "Failed to create task. Try again later.",
    "result_next": "What next?",
    "result_restart": "🆕 Start over",
    "result_repeat": "🔁 Repeat",
    "result_finish": "❌ Finish",
    "result_done": "✅ Done. To create more, use /start.",
    "repeat_prompt": (
        "Repeat this request?\n"
        "Credits to charge: {cost} {cost, plural, one {credit} other {credits}}."
    ),
    "repeat_send": "✅ Send",
    "repeat_cancel": "❌ Cancel",
    "repeat_cancelled": "Cancelled.",
    "error_banned": "Access denied.",
    "error_outputs": "Invalid output count.",
    "error_too_many": "Too many active tasks. Please wait.",
    "error_daily_cap": "Daily spending limit reached.",
    "error_no_credits": "Not enough credits.",
    "error_refs_required": "This mode requires at least one photo.",
    "error_generic": "Failed to start generation.",
    "help_text": "ℹ️ This bot generates images. Use the menu below.\nCommands: /start /ref CODE /promo CODE /admin (admins).",
    "start_hello": "👋 Hello, {name}!",
    "start_balance": "💰 Balance: <b>{credits}</b> {credits, plural, one {credit} other {credits}}.",
    "start_terms": "📜 By using the bot, you agree to follow the rules and laws.",
    "start_bonus": "Signup bonus: +{credits} {credits, plural, one {credit} other {credits}}.",
    "prices_title": "🧮 <b>Credit math</b>",
    "prices_note": "Prices are current at the time of request and update instantly after admin changes.",
    "prices_nb": "🍌 Nano Banana — <b>{cost}</b> cr.",
    "prices_edit": "🛠️ Nano Banana Edit — <b>{cost}</b> cr.",
    "prices_pro_no_refs_1k": "⭐ Pro without references 1K — <b>{cost}</b> cr.",
    "prices_pro_no_refs_2k": "⭐ Pro without references 2K — <b>{cost}</b> cr.",
    "prices_pro_no_refs_4k": "⭐ Pro without references 4K — <b>{cost}</b> cr.",
    "prices_pro_refs_1k": "📎 Pro with references 1K — <b>{cost}</b> cr.",
    "prices_pro_refs_2k": "📎 Pro with references 2K — <b>{cost}</b> cr.",
    "prices_pro_refs_4k": "📎 Pro with references 4K — <b>{cost}</b> cr.",
    "history_user_not_found": "User not found.",
    "history_empty_bot": "🕘 History is empty.",
    "history_not_found": "Not found",
    "history_no_access": "Not enough rights.",
    "history_not_ready": "⏳ Results are not ready yet.",
    "history_links_missing": "⚠️ Links not found.",
    "history_open_results": "Open results",
    "history_regen": "Regenerate",
    "history_regen_started": "✅ Regeneration started.",
    "history_created": "Created",
    "payment_packages_missing": "⚠️ Packages are not configured. Contact admin.",
    "payment_topup_choose": "💳 Choose top-up method:",
    "payment_topup_promo": "🎟️ Enter promo code",
    "payment_topup_stars": "⭐ Buy with Stars",
    "payment_promo_enter": "Send promo code only, without extra characters.",
    "payment_promo_cancel": "❌ Cancel",
    "payment_promo_cancelled": "Promo code entry cancelled.",
    "payment_choose": "💳 Choose a package:",
    "payment_package_not_found": "Package not found",
    "payment_invalid": "Invalid payment.",
    "payment_processed": "Payment already processed.",
    "payment_success": "Payment received. Credited {credits} {credits, plural, one {credit} other {credits}}.",
    "payment_user_not_found": "User not found.",
    "payment_desc": "{credits} {credits, plural, one {credit} other {credits}}",
    "ref_usage": "Usage: /ref CODE",
    "ref_not_found": "Code not found or inactive.",
    "ref_already": "Referral code already applied.",
    "ref_applied": "Referral code applied. Discount will be used in generations.",
    "promo_usage": "Usage: /promo CODE",
    "promo_invalid": "Promo code not found or inactive.",
    "promo_used": "Promo code already used.",
    "promo_not_found": "Promo code not found.",
    "promo_activated": "Promo code activated. Added {credits} {credits, plural, one {credit} other {credits}}.",
    "result_no_urls": "Generation finished but no URLs returned.",
    "result_original": "Original image",
    "result_caption": "Prompt: {prompt}\nMessage us if you need changes.",
    "result_send_failed": "Failed to send result. Try again later.",
    "generation_failed": "Generation failed.",
    "generation_failed_reason": "Generation failed. Reason: {reason}",
    "report_thanks": "Thanks for the report! We will review the result.",
}

BASE_ES: Dict[str, str] = dict(BASE_EN)
BASE_ES.update(
    {
        "site_tagline": "Genera imagenes directamente en tu navegador",
        "site_notice": (
            "Los resultados en el sitio se guardan no mas de 15 dias. "
            "Todas las generaciones tambien se duplican en el bot de Telegram Renderis Studio."
        ),
        "input_title": "Panel de generacion",
        "output_title": "Resultado",
        "output_empty": "Aun no hay resultados. Inicia una generacion.",
        "download": "Descargar",
        "delete": "Eliminar",
        "history_title": "Historial",
        "balance": "Saldo",
        "credits": "creditos",
        "topup_button": "Recargar",
        "topup_redeem_option": "Canjear codigo promo",
        "topup_stars_option": "Telegram Stars",
        "topup_crypto_option": "Cripto",
        "topup_modal_redeem_title": "Canjear codigo promo",
        "topup_modal_crypto_title": "Pago cripto",
        "topup_stars_redirect": "Abriendo Telegram para pago con Stars...",
        "topup_stars_unavailable": "El bot de Telegram no esta configurado.",
        "language": "Idioma",
        "redeem": "Codigo promo",
        "redeem_placeholder": "Introduce el codigo promo",
        "redeem_button": "Activar",
        "model_label": "Modelo",
        "model_tagline_nano_banana": "Generaciones rapidas a partir de tu descripcion.",
        "model_tagline_nano_banana_pro": "Mas detalle y calidad. Puedes agregar referencias.",
        "model_tagline_nano_banana_edit": "Edicion basada en tus fotos.",
        "prompt_label": "Prompt",
        "prompt_placeholder": "Describe lo que quieres crear...",
        "upload_label": "Referencias",
        "upload_hint": "Puedes subir hasta 8 imagenes (Edit: hasta 10).",
        "upload_hint_required": "Debes agregar al menos 1 imagen (hasta {max}).",
        "upload_hint_optional": "Puedes agregar hasta {max} imagenes.",
        "upload_required": "Agrega al menos una imagen.",
        "upload_count": "Seleccionadas {count} de {max} imagenes.",
        "upload_button": "Agregar archivos",
        "ref_images_title": "Imagenes de referencia {count}/{max}",
        "ref_images_note": "Puedes seleccionar hasta {max} imagenes para combinar",
        "ref_add": "Agregar foto",
        "ref_replace": "Reemplazar",
        "ref_add_sub": "Puedes subir varias, hasta 50MB",
        "options_label": "Opciones",
        "aspect_ratio": "Relacion de aspecto",
        "resolution": "Resolucion",
        "output_format": "Formato",
        "outputs": "Cantidad",
        "run": "Generar",
        "run_pending": "Enviando solicitud...",
        "history": "Historial",
        "history_empty": "Aun no hay generaciones.",
        "history_deleted": "Archivo eliminado",
        "logout": "Cerrar sesion",
        "login_title": "Iniciar sesion en Renderis",
        "login_subtitle": "Autoriza con Telegram para ver tu saldo e historial.",
        "login_failed": "No se pudo autorizar.",
        "login_required": "Inicia sesion con Telegram para continuar.",
        "prompt_required": "El prompt no puede estar vacio.",
        "request_sent": "Solicitud enviada.",
        "error_prefix": "Error",
        "promo_added": "Acreditado",
        "promo_error": "Error",
        "crypto_title": "Pago cripto",
        "crypto_select_package": "Selecciona un paquete",
        "crypto_create_invoice": "Crear factura",
        "crypto_open_invoice": "Abrir pago",
        "crypto_check_status": "Verificar pago",
        "crypto_loading_packages": "Cargando paquetes...",
        "crypto_packages_empty": "Aun no hay paquetes configurados.",
        "crypto_invoice_created": "Factura creada por {amount} {currency}. Paga y verifica el estado.",
        "crypto_waiting_payment": "Esperando confirmacion del pago...",
        "crypto_partial_payment": "Pago parcial detectado. Completa la factura.",
        "crypto_paid": "Pago confirmado. Se acreditaron {credits} creditos.",
        "crypto_canceled": "La factura fue cancelada o expiro.",
        "crypto_unavailable": "El pago cripto no esta disponible ahora.",
        "crypto_create_failed": "No se pudo crear la factura.",
        "crypto_status_failed": "No se pudo verificar el estado del pago.",
        "crypto_best_value": "Mejor precio",
        "crypto_bonus_badge": "Bono +{bonus}",
        "crypto_save_badge": "Ahorro {pct}%",
        "crypto_base_bonus_line": "Base {base} + bono {bonus}",
        "crypto_total_line": "Total {credits} creditos",
        "delete_failed": "No se pudo eliminar",
        "quote_line": "Se cobraran: {total} cr.",
        "quote_login_required": "Inicia sesion para ver el costo.",
        "quote_unavailable": "No se pudo calcular el costo.",
        "menu_buy": "💳 Recargar saldo",
        "payment_topup_choose": "💳 Elige metodo de recarga:",
        "payment_topup_promo": "🎟️ Introducir codigo promo",
        "payment_topup_stars": "⭐ Comprar con Stars",
        "payment_promo_enter": "Envia solo el codigo promo, sin simbolos adicionales.",
        "payment_promo_cancel": "❌ Cancelar",
        "payment_promo_cancelled": "Entrada de codigo promo cancelada.",
        "ratio_2_3": "2:3 (vertical)",
        "ratio_3_4": "3:4 (vertical)",
        "ratio_3_2": "3:2 (horizontal)",
        "ratio_4_3": "4:3 (horizontal)",
        "ratio_4_5": "4:5 (vertical)",
        "ratio_5_4": "5:4 (horizontal)",
        "ratio_9_16": "9:16 (vertical)",
        "ratio_16_9": "16:9 (horizontal)",
        "ratio_21_9": "21:9 (cinematico)",
    }
)


TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "ru": BASE_RU,
    "en": BASE_EN,
    "es": BASE_ES,
}


def t(lang: str, key: str) -> str:
    normalized = normalize_lang(lang)
    return TRANSLATIONS.get(normalized, BASE_RU).get(key, BASE_RU.get(key, key))


def _extract_braced(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx], idx + 1
    return None


def _split_top_level(text: str, separator: str, limit: int = -1) -> list[str]:
    parts: list[str] = []
    start = 0
    splits = 0
    depth = 0
    for idx, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0 and (limit < 0 or splits < limit):
            parts.append(text[start:idx])
            start = idx + 1
            splits += 1
    parts.append(text[start:])
    return parts


def _parse_icu_forms(text: str) -> dict[str, str]:
    forms: dict[str, str] = {}
    idx = 0
    length = len(text)
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break

        key_start = idx
        while idx < length and not text[idx].isspace() and text[idx] != "{":
            idx += 1
        key = text[key_start:idx].strip()
        while idx < length and text[idx].isspace():
            idx += 1
        if not key or idx >= length or text[idx] != "{":
            break

        extracted = _extract_braced(text, idx)
        if not extracted:
            break
        body, next_idx = extracted
        forms[key] = body
        idx = next_idx
    return forms


def _plural_category(lang: str, value: float) -> str:
    normalized = normalize_lang(lang)
    if not float(value).is_integer():
        return "other"
    n = abs(int(value))
    if normalized == "ru":
        mod10 = n % 10
        mod100 = n % 100
        if mod10 == 1 and mod100 != 11:
            return "one"
        if mod10 in (2, 3, 4) and mod100 not in (12, 13, 14):
            return "few"
        if mod10 == 0 or mod10 in (5, 6, 7, 8, 9) or mod100 in (11, 12, 13, 14):
            return "many"
        return "other"
    return "one" if n == 1 else "other"


def _render_icu_token(token: str, lang: str, params: dict[str, object]) -> str:
    parts = _split_top_level(token, ",", limit=2)
    if len(parts) < 3:
        return "{" + token + "}"

    var_name = parts[0].strip()
    token_type = parts[1].strip()
    body = parts[2].strip()
    forms = _parse_icu_forms(body)
    if not var_name or not forms:
        return "{" + token + "}"

    if token_type == "plural":
        raw_value = params.get(var_name)
        try:
            value = float(raw_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "{" + token + "}"

        selected = None
        if float(value).is_integer():
            selected = forms.get(f"={int(value)}")
        if selected is None:
            selected = forms.get(_plural_category(lang, value))
        if selected is None:
            selected = forms.get("other")
        if selected is None:
            return ""

        value_str = str(int(value)) if float(value).is_integer() else str(value)
        return _render_icu(selected.replace("#", value_str), lang, params)

    if token_type == "select":
        selector = str(params.get(var_name, "other"))
        selected = forms.get(selector, forms.get("other", ""))
        return _render_icu(selected, lang, params)

    return "{" + token + "}"


def _render_icu(text: str, lang: str, params: dict[str, object]) -> str:
    out: list[str] = []
    idx = 0
    while idx < len(text):
        if text[idx] != "{":
            out.append(text[idx])
            idx += 1
            continue

        extracted = _extract_braced(text, idx)
        if not extracted:
            out.append(text[idx])
            idx += 1
            continue
        token, next_idx = extracted
        out.append(_render_icu_token(token, lang, params))
        idx = next_idx
    return "".join(out)


def tf(lang: str, key: str, **kwargs: object) -> str:
    base = t(lang, key)
    rendered = _render_icu(base, lang, kwargs)
    try:
        return rendered.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return rendered
