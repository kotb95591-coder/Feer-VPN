"""Vercel serverless entrypoint для Telegram webhook.

Vercel маршрутизирует запросы на /api/webhook сюда. Каждый вызов — отдельный
холодный/тёплый запуск, поэтому бот работает без постоянного polling.
Фоновые задачи выполняются через api/cron.py (Vercel Cron).
"""
import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler

from aiogram.types import Update

from config import config
from bot import create_bot, create_dispatcher
from db.base import init_db

_initialized = False
_dispatcher = None


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


def _get_dispatcher():
    """Dispatcher (с роутерами) создаём один раз на инстанс и кэшируем.

    Роутеры в aiogram — модульные синглтоны: повторный include_router в
    новый Dispatcher на «тёплом» инстансе Vercel падает с
    RuntimeError: Router is already attached. Поэтому кэшируем Dispatcher.
    Bot при этом создаём заново на каждый запрос (его aiohttp-сессия
    привязана к event loop конкретного asyncio.run и закрывается в конце).
    """
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = create_dispatcher()
    return _dispatcher


async def _process(update_data: dict) -> None:
    bot = create_bot()
    dp = _get_dispatcher()
    try:
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        # Любую ошибку обработки логируем, но НЕ пробрасываем наружу: иначе
        # do_POST вернёт 500, и Telegram будет бесконечно перевыдавать тот же
        # (часто уже устаревший) апдейт — лавина повторов. Пользователь всегда
        # может нажать кнопку ещё раз.
        logging.exception("Ошибка обработки апдейта Telegram")
    finally:
        await bot.session.close()


class handler(BaseHTTPRequestHandler):
    """Vercel Python serverless handler."""

    def _reply(self, code: int, body: str = "ok") -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:  # health check
        self._reply(200, "Feer VPN webhook alive")

    def do_POST(self) -> None:
        # проверка секретного токена Telegram
        if config.WEBHOOK_SECRET:
            token = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if token != config.WEBHOOK_SECRET:
                self._reply(403, "forbidden")
                return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            update_data = json.loads(raw.decode())
        except Exception:
            self._reply(400, "bad request")
            return

        _ensure_init()
        asyncio.run(_process(update_data))
        self._reply(200)
