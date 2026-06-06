"""Оркестрация подписки: связывает БД, Marzban и выдачу ключа.

Одна подписка = один Marzban-юзер = один VLESS-ключ (Семья — тот же ключ на ≤ 3 устройствах).
"""
import logging

from config import config, get_tariff
from db import repo
from db.models import Subscription, User
from services.marzban import MarzbanError, marzban
from utils.helpers import gen_marzban_username

log = logging.getLogger(__name__)


class SubscriptionError(Exception):
    pass


async def issue_or_extend(
    user: User, plan: str, bonus_days: int = 0
) -> tuple[Subscription, bool]:
    """Выдаёт новую подписку (с ключом) или продлевает существующую того же тарифа.

    Возвращает (subscription, is_new).
    """
    tariff = get_tariff(plan)
    if not tariff:
        raise SubscriptionError(f"Неизвестный тариф: {plan}")

    days = tariff["days"] + bonus_days
    existing = await repo.get_active_subscription(user.id)

    if existing and existing.plan == plan and existing.marzban_username:
        # продлеваем тот же ключ
        try:
            await marzban.renew(existing.marzban_username, days)
        except MarzbanError as e:
            raise SubscriptionError(f"Marzban: {e}") from e
        await repo.extend_subscription(existing.id, days)
        log.info("Подписка %s продлена на %s дн.", existing.id, days)
        return existing, False

    # новая подписка
    sub = await repo.create_subscription(user.id, plan, tariff["devices"])
    username = gen_marzban_username(user.tg_id)
    try:
        await marzban.create_user(username, days, tariff["devices"])
        vless = await marzban.get_vless_link(username)
    except MarzbanError as e:
        await repo.set_subscription_status(sub.id, "expired")
        raise SubscriptionError(f"Marzban: {e}") from e

    sub = await repo.activate_subscription(sub.id, username, vless, days)
    log.info("Выдана новая подписка %s (%s)", sub.id, username)
    return sub, True


async def admin_grant(
    user: User, plan: str, days: int | None = None
) -> tuple[Subscription, bool]:
    """Выдаёт/продлевает подписку вручную (админом), без оплаты.

    days=None — стандартный срок тарифа. Иначе — ровно days дней.
    Внутри использует ту же логику выдачи ключа, что и при оплате.
    """
    tariff = get_tariff(plan)
    if not tariff:
        raise SubscriptionError(f"Неизвестный тариф: {plan}")
    bonus_days = 0
    if days is not None:
        bonus_days = days - tariff["days"]
    return await issue_or_extend(user, plan, bonus_days=bonus_days)


async def disable_expired() -> int:
    """Отключает истёкшие подписки в Marzban и в БД."""
    subs = await repo.expired_subscriptions()
    count = 0
    for sub in subs:
        await repo.set_subscription_status(sub.id, "expired")
        if sub.marzban_username:
            try:
                await marzban.set_status(sub.marzban_username, "disabled")
            except MarzbanError as e:
                log.error("Не удалось отключить %s: %s", sub.marzban_username, e)
        count += 1
    return count


async def unban_account(user: User) -> None:
    """Снимает бан с аккаунта (после оплаты разбана или вручную).

    Обнуляет счётчик нарушений и статус. Подписки остаются banned — новая выдаётся отдельно.
    """
    await repo.set_user_status(user.tg_id, "ok", None)
    # обнуляем нарушения аккаунта
    fresh = await repo.get_user(user.tg_id)
    if fresh:
        await repo.run(lambda s: _reset_violations(s, fresh.id))


def _reset_violations(session, user_id: int) -> None:
    from db.models import User as U

    u = session.get(U, user_id)
    if u:
        u.violations = 0
