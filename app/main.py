from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import admin, generate, history, misc, payments, referral_promo, start
from app.bot.middleware import DbSessionMiddleware
from app.config import get_settings
from app.db.session import create_sessionmaker
from app.services.kie_client import KieClient
from app.services.poller import PollManager
from app.services.poller_runtime import set_poller
from app.utils.logging import configure_logging, get_logger


logger = get_logger('main')


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    sessionmaker = create_sessionmaker()
    dp.update.middleware(DbSessionMiddleware(sessionmaker))

    dp.include_router(start.router)
    dp.include_router(generate.router)
    dp.include_router(history.router)
    dp.include_router(payments.router)
    dp.include_router(referral_promo.router)
    dp.include_router(admin.router)
    dp.include_router(misc.router)

    kie_client = KieClient()
    poller = PollManager(bot, sessionmaker, kie_client)
    set_poller(poller)

    await poller.restore_pending()

    try:
        await dp.start_polling(bot)
    finally:
        await kie_client.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
