"""Слой доступа к данным. Синхронный SQLAlchemy оборачивается в asyncio.to_thread,
чтобы не блокировать event loop бота."""
import asyncio
from datetime import datetime, timedelta
from typing import Callable, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import config
from db.base import SessionLocal
from db.models import (
    ConnectionLog,
    Device,
    Payment,
    Promocode,
    PromoRedemption,
    Setting,
    Subscription,
    User,
)

T = TypeVar("T")


async def run(fn: Callable[[Session], T]) -> T:
    """Выполнить fn(session) в отдельном потоке с авто-commit/rollback."""

    def _wrapper() -> T:
        with SessionLocal() as session:
            try:
                result = fn(session)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    return await asyncio.to_thread(_wrapper)


# ---------------- Users ----------------

async def get_or_create_user(tg_id: int, username: str | None, referrer_id: int | None = None) -> User:
    def _fn(s: Session) -> User:
        user = s.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            user = User(tg_id=tg_id, username=username, referrer_id=referrer_id)
            s.add(user)
            s.flush()
        elif username and user.username != username:
            user.username = username
        return user

    return await run(_fn)


async def get_user(tg_id: int) -> User | None:
    return await run(lambda s: s.scalar(select(User).where(User.tg_id == tg_id)))


async def get_user_by_id(user_id: int) -> User | None:
    return await run(lambda s: s.get(User, user_id))


async def set_user_status(tg_id: int, status: str, reason: str | None = None) -> None:
    def _fn(s: Session) -> None:
        user = s.scalar(select(User).where(User.tg_id == tg_id))
        if user:
            user.status = status
            user.ban_reason = reason

    await run(_fn)


async def add_user_violation(user_id: int) -> int:
    """+1 нарушение аккаунту, возвращает новое кол-во."""

    def _fn(s: Session) -> int:
        user = s.get(User, user_id)
        if not user:
            return 0
        user.violations += 1
        return user.violations

    return await run(_fn)


async def count_users() -> int:
    return await run(lambda s: s.scalar(select(func.count(User.id))) or 0)


async def list_users(limit: int = 50, offset: int = 0) -> list[User]:
    return await run(
        lambda s: list(
            s.scalars(select(User).order_by(User.created_at.desc()).limit(limit).offset(offset))
        )
    )


# ---------------- Subscriptions ----------------

async def create_subscription(user_id: int, plan: str, device_limit: int) -> Subscription:
    def _fn(s: Session) -> Subscription:
        sub = Subscription(
            user_id=user_id,
            plan=plan,
            device_limit=device_limit,
            status="pending",
        )
        s.add(sub)
        s.flush()
        return sub

    return await run(_fn)


async def get_subscription(sub_id: int) -> Subscription | None:
    return await run(lambda s: s.get(Subscription, sub_id))


async def get_active_subscription(user_id: int) -> Subscription | None:
    def _fn(s: Session) -> Subscription | None:
        return s.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .order_by(Subscription.expires_at.desc())
        )

    return await run(_fn)


async def get_subscription_by_marzban(username: str) -> Subscription | None:
    return await run(
        lambda s: s.scalar(
            select(Subscription).where(Subscription.marzban_username == username)
        )
    )


async def activate_subscription(
    sub_id: int,
    marzban_username: str,
    vless_key: str,
    days: int,
) -> Subscription | None:
    def _fn(s: Session) -> Subscription | None:
        sub = s.get(Subscription, sub_id)
        if not sub:
            return None
        now = datetime.utcnow()
        base = sub.expires_at if (sub.expires_at and sub.expires_at > now) else now
        sub.marzban_username = marzban_username
        sub.vless_key = vless_key
        sub.status = "active"
        sub.paid_at = now
        sub.expires_at = base + timedelta(days=days)
        return sub

    return await run(_fn)


async def extend_subscription(sub_id: int, days: int) -> Subscription | None:
    def _fn(s: Session) -> Subscription | None:
        sub = s.get(Subscription, sub_id)
        if not sub:
            return None
        now = datetime.utcnow()
        base = sub.expires_at if (sub.expires_at and sub.expires_at > now) else now
        sub.expires_at = base + timedelta(days=days)
        sub.status = "active"
        return sub

    return await run(_fn)


async def set_subscription_status(sub_id: int, status: str) -> None:
    def _fn(s: Session) -> None:
        sub = s.get(Subscription, sub_id)
        if sub:
            sub.status = status

    await run(_fn)


async def add_subscription_violation(sub_id: int) -> int:
    def _fn(s: Session) -> int:
        sub = s.get(Subscription, sub_id)
        if not sub:
            return 0
        sub.violations += 1
        return sub.violations

    return await run(_fn)


async def expiring_subscriptions(in_days: int) -> list[Subscription]:
    """Подписки, истекающие ровно через in_days (по дате)."""

    def _fn(s: Session) -> list[Subscription]:
        now = datetime.utcnow()
        start = now + timedelta(days=in_days)
        end = start + timedelta(days=1)
        return list(
            s.scalars(
                select(Subscription).where(
                    Subscription.status == "active",
                    Subscription.expires_at >= start,
                    Subscription.expires_at < end,
                )
            )
        )

    return await run(_fn)


async def expired_subscriptions() -> list[Subscription]:
    def _fn(s: Session) -> list[Subscription]:
        now = datetime.utcnow()
        return list(
            s.scalars(
                select(Subscription).where(
                    Subscription.status == "active",
                    Subscription.expires_at < now,
                )
            )
        )

    return await run(_fn)


async def all_active_subscriptions() -> list[Subscription]:
    return await run(
        lambda s: list(
            s.scalars(select(Subscription).where(Subscription.status == "active"))
        )
    )


# ---------------- Devices ----------------

async def upsert_device(
    subscription_id: int, hwid: str | None, ip: str | None
) -> tuple[Device, bool]:
    """Добавляет/обновляет устройство. Возвращает (device, is_new)."""

    def _fn(s: Session) -> tuple[Device, bool]:
        now = datetime.utcnow()
        q = select(Device).where(Device.subscription_id == subscription_id)
        if hwid:
            q = q.where(Device.hwid == hwid)
        else:
            q = q.where(Device.first_ip == ip)
        device = s.scalar(q)
        is_new = False
        if device is None:
            device = Device(
                subscription_id=subscription_id,
                hwid=hwid,
                first_ip=ip,
                last_ip=ip,
                last_seen=now,
                status="active",
            )
            s.add(device)
            s.flush()
            is_new = True
        else:
            device.last_ip = ip
            device.last_seen = now
        return device, is_new

    return await run(_fn)


async def list_devices(subscription_id: int) -> list[Device]:
    return await run(
        lambda s: list(
            s.scalars(select(Device).where(Device.subscription_id == subscription_id))
        )
    )


async def active_device_count(subscription_id: int) -> int:
    return await run(
        lambda s: s.scalar(
            select(func.count(Device.id)).where(
                Device.subscription_id == subscription_id,
                Device.status == "active",
            )
        )
        or 0
    )


async def ban_device(device_id: int) -> None:
    def _fn(s: Session) -> None:
        device = s.get(Device, device_id)
        if device:
            device.status = "banned"

    await run(_fn)


# ---------------- Connection log ----------------

async def log_connection(subscription_id: int, ip: str | None, hwid: str | None) -> None:
    def _fn(s: Session) -> None:
        s.add(ConnectionLog(subscription_id=subscription_id, ip=ip, hwid=hwid))

    await run(_fn)


async def count_unique_connections(subscription_id: int, hours: int) -> int:
    """Сколько уникальных (hwid или ip) за окно."""

    def _fn(s: Session) -> int:
        since = datetime.utcnow() - timedelta(hours=hours)
        key = func.coalesce(ConnectionLog.hwid, ConnectionLog.ip)
        return (
            s.scalar(
                select(func.count(func.distinct(key))).where(
                    ConnectionLog.subscription_id == subscription_id,
                    ConnectionLog.created_at >= since,
                )
            )
            or 0
        )

    return await run(_fn)


# ---------------- Payments ----------------

async def create_payment(
    user_id: int,
    amount: float,
    code: str,
    type_: str,
    plan: str | None = None,
    promocode: str | None = None,
) -> Payment:
    def _fn(s: Session) -> Payment:
        p = Payment(
            user_id=user_id,
            amount=amount,
            code=code,
            type=type_,
            plan=plan,
            promocode=promocode,
            status="pending",
        )
        s.add(p)
        s.flush()
        return p

    return await run(_fn)


async def get_payment(payment_id: int) -> Payment | None:
    return await run(lambda s: s.get(Payment, payment_id))


async def get_pending_payment_by_code(code: str) -> Payment | None:
    return await run(
        lambda s: s.scalar(
            select(Payment).where(Payment.code == code, Payment.status == "pending")
        )
    )


async def pending_payments() -> list[Payment]:
    return await run(
        lambda s: list(s.scalars(select(Payment).where(Payment.status == "pending")))
    )


async def da_id_used(da_id: str) -> bool:
    return await run(
        lambda s: s.scalar(select(func.count(Payment.id)).where(Payment.da_id == da_id)) > 0
    )


async def mark_payment_paid(payment_id: int, da_id: str | None) -> Payment | None:
    def _fn(s: Session) -> Payment | None:
        p = s.get(Payment, payment_id)
        if not p:
            return None
        p.status = "paid"
        p.da_id = da_id
        p.paid_at = datetime.utcnow()
        return p

    return await run(_fn)


async def expire_old_payments(minutes: int) -> int:
    def _fn(s: Session) -> int:
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        rows = list(
            s.scalars(
                select(Payment).where(
                    Payment.status == "pending", Payment.created_at < cutoff
                )
            )
        )
        for p in rows:
            p.status = "expired"
        return len(rows)

    return await run(_fn)


async def revenue_total() -> float:
    return await run(
        lambda s: float(
            s.scalar(select(func.sum(Payment.amount)).where(Payment.status == "paid")) or 0
        )
    )


# ---------------- Promocodes ----------------

async def get_promocode(code: str) -> Promocode | None:
    return await run(
        lambda s: s.scalar(select(Promocode).where(Promocode.code == code.upper()))
    )


async def create_promocode(
    code: str,
    type_: str,
    value: float,
    usage_limit: int = 0,
    only_new: bool = False,
    expires_at: datetime | None = None,
) -> Promocode:
    def _fn(s: Session) -> Promocode:
        promo = Promocode(
            code=code.upper(),
            type=type_,
            value=value,
            usage_limit=usage_limit,
            only_new=only_new,
            expires_at=expires_at,
        )
        s.add(promo)
        s.flush()
        return promo

    return await run(_fn)


async def list_promocodes() -> list[Promocode]:
    return await run(
        lambda s: list(s.scalars(select(Promocode).order_by(Promocode.created_at.desc())))
    )


async def set_promocode_active(code: str, active: bool) -> None:
    def _fn(s: Session) -> None:
        promo = s.scalar(select(Promocode).where(Promocode.code == code.upper()))
        if promo:
            promo.active = active

    await run(_fn)


async def redeem_promocode(promo_id: int, user_id: int) -> None:
    def _fn(s: Session) -> None:
        promo = s.get(Promocode, promo_id)
        if promo:
            promo.used_count += 1
            s.add(PromoRedemption(promo_id=promo_id, user_id=user_id))

    await run(_fn)


async def user_used_promo(promo_id: int, user_id: int) -> bool:
    return await run(
        lambda s: s.scalar(
            select(func.count(PromoRedemption.id)).where(
                PromoRedemption.promo_id == promo_id,
                PromoRedemption.user_id == user_id,
            )
        )
        > 0
    )


async def user_has_any_subscription(user_id: int) -> bool:
    return await run(
        lambda s: s.scalar(
            select(func.count(Subscription.id)).where(Subscription.user_id == user_id)
        )
        > 0
    )


# ---------------- Settings (key-value) ----------------

async def get_setting(key: str) -> str | None:
    def _fn(s: Session) -> str | None:
        row = s.get(Setting, key)
        return row.value if row else None

    return await run(_fn)


async def set_setting(key: str, value: str) -> None:
    def _fn(s: Session) -> None:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value

    await run(_fn)
