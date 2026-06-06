"""Vercel serverless entrypoint для Telegram webhook.

Vercel маршрутизирует запросы на /api/webhook сюда. Каждый вызов — отдельный
холодный/тёплый запуск, поэтому бот работает без постоянного polling.
Фоновые задачи выполняются через api/cron.py (Vercel Cron).
"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler

from aiogram.types import Update

from config import config
from bot import create_bot, create_dispatcher
from db.base import init_db

_initialized = False


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


async def _process(update_data: dict) -> None:
    bot = create_bot()
    dp = create_dispatcher()
    try:
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
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
