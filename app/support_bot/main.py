from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import get_settings
from app.db.session import create_sessionmaker
from app.support_bot.handlers import router
from app.bot.middleware import DbSessionMiddleware
from app.utils.logging import configure_logging, get_logger


logger = get_logger("support-bot")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.support_bot_token:
        logger.warning("SUPPORT_BOT_TOKEN не задан, бот поддержки не запущен.")
        return

    bot = Bot(
        token=settings.support_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    sessionmaker = create_sessionmaker()
    dp.update.middleware(DbSessionMiddleware(sessionmaker))
    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
