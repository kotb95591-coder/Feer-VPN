"""Антифрод и система банов.

Правила (из ТЗ):
1. Превышение лимита устройств (>1 Solo / >3 Семья):
   Лишнее устройство отключается и банится по HWID/UUID. Если определить лишнее
   невозможно — банится вся подписка (= 1 нарушение).
2. >MAX_UNIQUE_USERS разных пользователей/IP на одной подписке → подписка банится (= 1 нарушение).
3. 2 нарушения → бан всего аккаунта. Разбан: 300 ₽ (включает Solo) или через саппорт.
"""
import logging
from dataclasses import dataclass

from config import config
from db import repo
from db.models import Subscription
from services.marzban import MarzbanError, marzban

log = logging.getLogger(__name__)


@dataclass
class FraudOutcome:
    violated: bool
    subscription_banned: bool = False
    account_banned: bool = False
    reason: str = ""
    banned_devices: int = 0


async def register_connection(
    subscription: Subscription, hwid: str | None, ip: str | None
) -> FraudOutcome:
    """Вызывается при обнаружении подключения (из проверки Marzban).

    Логирует подключение, обновляет устройства и проверяет лимиты.
    """
    await repo.log_connection(subscription.id, ip, hwid)
    device, is_new = await repo.upsert_device(subscription.id, hwid, ip)

    # Правило 1: превышение лимита устройств
    active = await repo.active_device_count(subscription.id)
    if active > subscription.device_limit:
        # лишнее определено (только что подключившееся) — баним устройство
        if is_new and hwid:
            await repo.ban_device(device.id)
            return await _apply_violation(
                subscription,
                reason=f"Превышен лимит устройств ({active}/{subscription.device_limit}), лишнее устройство забанено",
                banned_devices=1,
                ban_subscription=False,
            )
        # не смогли определить лишнее — баним всю подписку
        return await _apply_violation(
            subscription,
            reason="Превышен лимит устройств (лишнее не определено)",
            ban_subscription=True,
        )

    # Правило 2: слишком много уникальных подключений
    unique = await repo.count_unique_connections(
        subscription.id, config.ANTIFRAUD_WINDOW_HOURS
    )
    if unique > config.MAX_UNIQUE_USERS:
        return await _apply_violation(
            subscription,
            reason=f"Расшаривание: {unique} уникальных подключений > {config.MAX_UNIQUE_USERS}",
            ban_subscription=True,
        )

    return FraudOutcome(violated=False)


async def _apply_violation(
    subscription: Subscription,
    reason: str,
    ban_subscription: bool = False,
    banned_devices: int = 0,
) -> FraudOutcome:
    """Регистрирует 1 нарушение и применяет санкции."""
    log.warning("Антифрод sub=%s: %s", subscription.id, reason)
    await repo.add_subscription_violation(subscription.id)

    outcome = FraudOutcome(
        violated=True, reason=reason, banned_devices=banned_devices
    )

    if ban_subscription:
        await repo.set_subscription_status(subscription.id, "banned")
        if subscription.marzban_username:
            try:
                await marzban.ban(subscription.marzban_username)
            except MarzbanError as e:
                log.error("Не удалось забанить в Marzban: %s", e)
        outcome.subscription_banned = True

    # Счётчик нарушений аккаунта
    total = await repo.add_user_violation(subscription.user_id)
    if total >= config.VIOLATIONS_TO_BAN_ACCOUNT:
        await _ban_account(subscription.user_id, f"{config.VIOLATIONS_TO_BAN_ACCOUNT} нарушения")
        outcome.account_banned = True

    return outcome


async def _ban_account(user_id: int, reason: str) -> None:
    """Банит весь аккаунт и все его подписки в Marzban."""
    user = await repo.get_user_by_id(user_id)
    if not user:
        return
    await repo.set_user_status(user.tg_id, "banned", reason)
    for sub in user.subscriptions:
        await repo.set_subscription_status(sub.id, "banned")
        if sub.marzban_username:
            try:
                await marzban.ban(sub.marzban_username)
            except MarzbanError as e:
                log.error("Бан Marzban при бане аккаунта не удался: %s", e)
    log.warning("Аккаунт user_id=%s забанен: %s", user_id, reason)


async def scan_all_subscriptions() -> list[FraudOutcome]:
    """Периодическая проверка (вызывается планировщиком).

    Опрашивает Marzban по активным IP по каждой подписке и прогоняет антифрод.
    """
    outcomes: list[FraudOutcome] = []
    subs = await repo.all_active_subscriptions()
    for sub in subs:
        if not sub.marzban_username:
            continue
        try:
            ips = await marzban.get_active_ips(sub.marzban_username)
        except MarzbanError as e:
            log.error("Не удалось получить IP для %s: %s", sub.marzban_username, e)
            continue
        for ip in ips:
            outcome = await register_connection(sub, hwid=None, ip=ip)
            if outcome.violated:
                outcomes.append(outcome)
                break  # подписка уже наказана
    return outcomes
