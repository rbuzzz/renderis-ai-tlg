# Renderis AI Telegram Bot

Телеграм-бот для генерации изображений через Kie.ai с оплатой Telegram Stars, кредитами, рефералками, промокодами и админ‑панелью в Telegram.

## Возможности
- Генерация через Kie.ai (Nano Banana / Nano Banana Pro)
- Кредиты и журнал транзакций (append-only)
- Платежи Telegram Stars (XTR)
- Реферальные и промо‑коды
- История генераций, регенерация
- Админ‑панель в Telegram
- Возобновление незавершенных задач после перезапуска

## Требования
- Python 3.11+
- PostgreSQL 15+
- Telegram bot token
- Kie.ai API key

## Локальный запуск
1. Создайте виртуальное окружение и установите зависимости:

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

2. Поднимите Postgres:

```bash
docker-compose up -d
```

3. Скопируйте `.env.example` в `.env` и заполните:

```bash
copy .env.example .env
```

4. Примените миграции:

```bash
alembic upgrade head
```

5. Заполните базовые цены и пакеты (пример):

```bash
python -m app.scripts.seed
```

6. Запустите бота:

```bash
python -m app.main
```

## Настройка Telegram Stars
- Валюта: `XTR`
- Для Stars `STARS_PROVIDER_TOKEN` можно оставить пустым.

## Админ‑команды
- `/admin` — админ‑панель
- `/ref CODE` — применить рефкод
- `/promo CODE` — применить промо‑код

## Деплой на Ubuntu
1. Создайте пользователя и папку:

```bash
sudo adduser --system --group renderisbot
sudo mkdir -p /opt/renderis-ai-telegram-bot
sudo chown -R renderisbot:renderisbot /opt/renderis-ai-telegram-bot
```

2. Клонируйте репозиторий и установите зависимости:

```bash
cd /opt/renderis-ai-telegram-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Создайте `/etc/renderis-bot.env` (на основе `.env.example`).

4. Примените миграции и сиды:

```bash
alembic upgrade head
python -m app.scripts.seed
```

5. Установите systemd‑сервис:

```bash
sudo cp systemd/telegram-ai-bot.service /etc/systemd/system/telegram-ai-bot.service
sudo systemctl daemon-reload
sudo systemctl enable telegram-ai-bot
sudo systemctl start telegram-ai-bot
```

6. Логи:

```bash
sudo journalctl -u telegram-ai-bot -f
```

## Резервное копирование Postgres
Минимальный вариант:

```bash
pg_dump -U bot -h localhost renderis_bot > backup.sql
```

## Добавление новой модели
1. Создайте файл в `app/modelspecs/`.
2. Добавьте модель в `app/modelspecs/registry.py`.
3. Добавьте цены в таблицу `prices` (или используйте `app/scripts/seed.py`).

## Примечания
- Референс‑изображения для Nano Banana Pro временно выключены. Архитектура готова к добавлению S3/R2 и передачи URL.
- В `.env` можно задать `NSFW_BLOCKLIST` (через запятую) для простого стоп‑листа.
- Все сообщения пользователю на русском.
