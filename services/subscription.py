"""Оркестрация подписки: связывает БД, Marzban и выдачу ключа.

Одна подписка = один Marzban-юзер = один VLESS-ключ (Семья — тот же ключ на ≤ 3 устройствах).
"""
import logging

from config import config, get_tariff
from db import repo
from db.models import Subscription, User
from services.marzban import MarzbanError, marzban
from utils.helpers import gen_marzban_username, plan_title

log = logging.getLogger(__name__)


class SubscriptionError(Exception):
    pass


class InsufficientFundsError(SubscriptionError):
    """Недостаточно средств на балансе для покупки/продления."""

    def __init__(self, need: float, have: float) -> None:
        self.need = need
        self.have = have
        super().__init__(
            f"Недостаточно средств: нужно {need:.0f} ₽, на балансе {have:.0f} ₽"
        )


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
        # пробуем продлить тот же ключ
        try:
            await marzban.renew(existing.marzban_username, days)
        except MarzbanError as e:
            # ключ в Marzban отсутствует/битый — перевыпускаем на той же подписке
            log.warning(
                "Продление ключа %s не удалось (%s) — выпускаю заново",
                existing.marzban_username,
                e,
            )
            try:
                username, vless = await _create_key(user, plan, days)
            except MarzbanError as e2:
                raise SubscriptionError(f"Marzban: {e2}") from e2
            sub = await repo.activate_subscription(existing.id, username, vless, days)
            return (sub or existing), False
        await repo.extend_subscription(existing.id, days)
        log.info("Подписка %s продлена на %s дн.", existing.id, days)
        return existing, False

    # новая подписка
    sub = await repo.create_subscription(user.id, plan, tariff["devices"])
    try:
        username, vless = await _create_key(user, plan, days)
    except MarzbanError as e:
        await repo.set_subscription_status(sub.id, "expired")
        raise SubscriptionError(f"Marzban: {e}") from e

    sub = await repo.activate_subscription(sub.id, username, vless, days)
    log.info("Выдана новая подписка %s (%s)", sub.id, username)
    return sub, True


async def _create_key(user: User, plan: str, days: int) -> tuple[str, str]:
    """Создаёт юзера в Marzban и возвращает (username, vless_link).

    Ссылку берём прямо из ответа на создание (POST уже содержит links),
    без отдельного GET — чтобы не упираться в гонку read-after-write
    (404 сразу после создания).
    """
    tariff = get_tariff(plan)
    username = gen_marzban_username(user.tg_id)
    created = await marzban.create_user(username, days, tariff["devices"])
    vless = marzban.link_from_user(created)
    if not vless:
        vless = await marzban.get_vless_link(username)
    return username, vless


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


async def buy_with_balance(
    user: User, plan: str, price: float, bonus_days: int = 0
) -> tuple[Subscription, bool]:
    """Покупает/продлевает подписку, списывая стоимость с баланса.

    price — итоговая цена (уже с учётом промокода). При ошибке выдачи ключа
    деньги возвращаются на баланс.
    """
    tariff = get_tariff(plan)
    if not tariff:
        raise SubscriptionError(f"Неизвестный тариф: {plan}")

    have = await repo.get_balance(user.id)
    ok = await repo.deduct_balance(
        user.id, float(price), "charge", f"Покупка подписки {plan_title(plan)}"
    )
    if not ok:
        raise InsufficientFundsError(float(price), have)

    try:
        sub, is_new = await issue_or_extend(user, plan, bonus_days)
    except SubscriptionError:
        await repo.add_balance(
            user.id, float(price), "refund", "Возврат: не удалось выдать ключ"
        )
        raise
    return sub, is_new


async def unban_with_balance(user: User) -> tuple[Subscription, bool]:
    """Разбан за счёт баланса (PRICE_UNBAN), включает выдачу Solo."""
    price = float(config.PRICE_UNBAN)
    have = await repo.get_balance(user.id)
    ok = await repo.deduct_balance(user.id, price, "charge", "Разбан аккаунта")
    if not ok:
        raise InsufficientFundsError(price, have)
    await unban_account(user)
    refreshed = await repo.get_user(user.tg_id)
    try:
        return await issue_or_extend(refreshed or user, "solo")
    except SubscriptionError:
        await repo.add_balance(user.id, price, "refund", "Возврат: разбан не выдал ключ")
        raise


async def resume_after_topup(user: User, bot=None) -> Subscription | None:
    """После пополнения пытается возобновить приостановленную подписку с баланса."""
    sub = await repo.get_resumable_subscription(user.id)
    if not sub:
        return None
    tariff = get_tariff(sub.plan)
    if not tariff:
        return None
    price = float(tariff["price"])
    days = tariff["days"]
    ok = await repo.deduct_balance(
        user.id, price, "charge", f"Возобновление {plan_title(sub.plan)}"
    )
    if not ok:
        return None
    await repo.extend_subscription(sub.id, days)
    if sub.marzban_username:
        try:
            await marzban.set_status(sub.marzban_username, "active")
        except MarzbanError:
            pass
        try:
            await marzban.renew(sub.marzban_username, days)
        except MarzbanError as e:
            log.error("Возобновление: не удалось продлить %s: %s", sub.marzban_username, e)
    refreshed = await repo.get_subscription(sub.id)
    if bot is not None:
        new_balance = await repo.get_balance(user.id)
        try:
            await bot.send_message(
                user.tg_id,
                f"✅ Подписка <b>{plan_title(sub.plan)}</b> возобновлена на {days} дн.\n"
                f"Списано: <b>{price:.0f} ₽</b> · остаток: <b>{new_balance:.0f} ₽</b>.",
            )
        except Exception:  # noqa: BLE001
            pass
    return refreshed or sub


async def auto_renew_due(bot=None) -> dict:
    """Автопродление истёкших подписок за счёт баланса.

    Для каждой истёкшей активной подписки пытаемся списать стоимость тарифа
    с баланса и продлить. Если средств не хватает — приостанавливаем доступ
    и уведомляем о необходимости пополнить баланс.
    """
    subs = await repo.expired_subscriptions()
    renewed = 0
    disabled = 0
    for sub in subs:
        tariff = get_tariff(sub.plan)
        price = float(tariff["price"]) if tariff else 0.0
        days = tariff["days"] if tariff else 30
        user = await repo.get_user_by_id(sub.user_id)
        if not user:
            continue
        ok = await repo.deduct_balance(
            user.id, price, "charge", f"Автопродление {plan_title(sub.plan)}"
        )
        if ok:
            await repo.extend_subscription(sub.id, days)
            if sub.marzban_username:
                try:
                    await marzban.renew(sub.marzban_username, days)
                except MarzbanError as e:
                    log.error("Автопродление: не удалось продлить %s: %s", sub.marzban_username, e)
            renewed += 1
            if bot is not None:
                new_balance = await repo.get_balance(user.id)
                try:
                    await bot.send_message(
                        user.tg_id,
                        f"🔄 Подписка <b>{plan_title(sub.plan)}</b> автоматически продлена на "
                        f"{days} дн.\nСписано: <b>{price:.0f} ₽</b> · остаток на балансе: "
                        f"<b>{new_balance:.0f} ₽</b>.",
                    )
                except Exception:  # noqa: BLE001
                    pass
        else:
            await repo.set_subscription_status(sub.id, "expired")
            if sub.marzban_username:
                try:
                    await marzban.set_status(sub.marzban_username, "disabled")
                except MarzbanError as e:
                    log.error("Не удалось отключить %s: %s", sub.marzban_username, e)
            disabled += 1
            if bot is not None:
                have = await repo.get_balance(user.id)
                try:
                    await bot.send_message(
                        user.tg_id,
                        f"⚠️ Подписка <b>{plan_title(sub.plan)}</b> приостановлена: на балансе "
                        f"<b>{have:.0f} ₽</b>, а для продления нужно <b>{price:.0f} ₽</b>.\n"
                        "Пополни баланс в личном кабинете — и доступ включится.",
                    )
                except Exception:  # noqa: BLE001
                    pass
    return {"renewed": renewed, "disabled": disabled}


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
