"""Фоновые задачи.

Два режима работы:
• polling/long-running — APScheduler запускает задачи по расписанию (start_scheduler).
• serverless (Vercel) — задачи вызываются из cron-эндпоинтов (run_due_tasks).
"""
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import config
from db import repo
from services import antifraud
from services import subscription as sub_service

log = logging.getLogger(__name__)


async def notify_expiring(bot: Bot) -> None:
    """Напоминания за 3 и 1 день до окончания."""
    for in_days in (3, 1):
        subs = await repo.expiring_subscriptions(in_days)
        for sub in subs:
            user = await repo.get_user_by_id(sub.user_id)
            if not user:
                continue
            try:
                await bot.send_message(
                    user.tg_id,
                    f"⏳ Твоя подписка истекает через {in_days} дн. "
                    "Продли её в меню, чтобы не потерять доступ.",
                )
            except Exception as e:
                log.warning("Не отправилось напоминание %s: %s", user.tg_id, e)


async def disable_expired() -> int:
    """Отключить истёкшие подписки."""
    count = await sub_service.disable_expired()
    if count:
        log.info("Отключено истёкших подписок: %s", count)
    return count


async def run_antifraud() -> int:
    """Прогнать антифрод по всем активным подпискам."""
    outcomes = await antifraud.scan_all_subscriptions()
    if outcomes:
        log.warning("Антифрод: нарушений выявлено %s", len(outcomes))
    return len(outcomes)


async def expire_payments() -> int:
    return await repo.expire_old_payments(config.PAYMENT_TIMEOUT_MIN)


async def run_due_tasks(bot: Bot, task: str = "all") -> dict:
    """Единая точка входа для Vercel Cron.

    task: all | reminders | expire | antifraud | payments
    """
    result: dict = {}
    if task in ("all", "reminders"):
        await notify_expiring(bot)
        result["reminders"] = "ok"
    if task in ("all", "expire"):
        result["expired"] = await disable_expired()
    if task in ("all", "antifraud"):
        result["fraud"] = await run_antifraud()
    if task in ("all", "payments"):
        result["payments_expired"] = await expire_payments()
    return result


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Запуск APScheduler для polling/long-running режима."""
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(notify_expiring, "cron", hour=12, minute=0, args=[bot])
    scheduler.add_job(disable_expired, "interval", minutes=30)
    scheduler.add_job(run_antifraud, "interval", minutes=15)
    scheduler.add_job(expire_payments, "interval", minutes=10)
    scheduler.start()
    log.info("Планировщик запущен")
    return scheduler
