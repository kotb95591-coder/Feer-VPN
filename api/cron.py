"""Vercel Cron entrypoint для фоновых задач (напоминания, антифрод, истечение).

Vercel Cron дёргает GET /api/cron по расписанию (см. vercel.json) и передаёт
заголовок Authorization: Bearer <CRON_SECRET>.
Параметр ?task=all|reminders|expire|antifraud|payments.
"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from config import config
from bot import create_bot
from db.base import init_db
from services.scheduler import run_due_tasks


async def _run(task: str) -> dict:
    init_db()
    bot = create_bot()
    try:
        return await run_due_tasks(bot, task)
    finally:
        await bot.session.close()


class handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:
        # авторизация cron
        if config.CRON_SECRET:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {config.CRON_SECRET}":
                self._reply(403, json.dumps({"error": "forbidden"}))
                return
        qs = parse_qs(urlparse(self.path).query)
        task = (qs.get("task", ["all"])[0])
        result = asyncio.run(_run(task))
        self._reply(200, json.dumps({"ok": True, "result": result}))
