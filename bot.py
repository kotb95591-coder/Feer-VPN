"""Точка входа Feer VPN бота. Поддерживает polling (локально) и webhook (Vercel/др.)."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from db.base import init_db
from handlers import setup_routers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("feervpn")


def create_bot() -> Bot:
    return Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    setup_routers(dp)
    return dp


async def run_polling() -> None:
    """Режим long-polling (для локального запуска и VPS)."""
    init_db()
    bot = create_bot()
    dp = create_dispatcher()

    # планировщик работает только в long-running режиме
    from services.scheduler import start_scheduler

    start_scheduler(bot)

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Бот запущен в режиме polling")
    await dp.start_polling(bot)


async def run_webhook() -> None:
    """Режим webhook на aiohttp (для VPS/контейнера с постоянным процессом).

    Для Vercel serverless используется api/webhook.py, а не эта функция.
    """
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    init_db()
    bot = create_bot()
    dp = create_dispatcher()

    await bot.set_webhook(
        url=config.WEBHOOK_URL,
        secret_token=config.WEBHOOK_SECRET or None,
        drop_pending_updates=True,
    )

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=config.WEBHOOK_SECRET or None
    ).register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    log.info("Бот запущен в режиме webhook на %s", config.WEBHOOK_URL)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBAPP_HOST, config.WEBAPP_PORT)
    await site.start()
    await asyncio.Event().wait()  # блокируемся навсегда


def main() -> None:
    try:
        if config.USE_WEBHOOK:
            asyncio.run(run_webhook())
        else:
            asyncio.run(run_polling())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")


if __name__ == "__main__":
    main()
