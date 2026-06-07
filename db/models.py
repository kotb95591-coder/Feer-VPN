"""Модели БД Feer VPN (одна подписка = один ключ + антифрод)."""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class User(Base):
    """Аккаунт клиента (привязан к tg_id)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # ok / banned
    status: Mapped[str] = mapped_column(String(16), default="ok")
    ban_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # суммарные нарушения по аккаунту (2 → бан)
    violations: Mapped[int] = mapped_column(Integer, default=0)
    # tg_id того, кто привёл (реферал)
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Баланс личного кабинета (₽). С него списываются подписки.
    balance: Mapped[float] = mapped_column(Float, default=0.0)

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Subscription(Base):
    """Подписка. Одна подписка = один Marzban-юзер = один VLESS-ключ."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # solo / family
    plan: Mapped[str] = mapped_column(String(16))
    # pending / active / expired / banned
    status: Mapped[str] = mapped_column(String(16), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # имя юзера в Marzban и его подписочная ссылка/ключ
    marzban_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    vless_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    # нарушения по этой подписке
    violations: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    devices: Mapped[list["Device"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )


class Device(Base):
    """Привязанное к подписке устройство (по HWID/UUID)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    hwid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    first_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # active / banned
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    subscription: Mapped["Subscription"] = relationship(back_populates="devices")


class ConnectionLog(Base):
    """Журнал подключений — для подсчёта уникальных HWID/IP."""

    __tablename__ = "connections_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hwid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Payment(Base):
    """Платёж (подписка или разбан), сверяется с DonationAlerts."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    # уникальный код, который клиент вставляет в сообщение доната
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # id доната в DonationAlerts (анти-дубль)
    da_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # subscription / unban
    type: Mapped[str] = mapped_column(String(16))
    plan: Mapped[str | None] = mapped_column(String(16), nullable=True)
    promocode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # pending / paid / expired
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="payments")


class Promocode(Base):
    """Промокод: процент / фикс / бонусные дни."""

    __tablename__ = "promocodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # percent / fixed / bonus_days
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    usage_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = без лимита
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    only_new: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PromoRedemption(Base):
    """Кто и какой промокод применил (чтобы не использовали дважды)."""

    __tablename__ = "promo_redemptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promocodes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Setting(Base):
    """Key-value хранилище (OAuth-токены DonationAlerts и прочее).

    Нужно, чтобы обновлённые токены переживали перезапуски и serverless
    (на Vercel файлы не сохраняются между вызовами).
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Transaction(Base):
    """История операций по балансу личного кабинета.

    amount > 0 — пополнение/бонус/возврат; amount < 0 — списание (покупка/разбан).
    """

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    # topup / charge / refund / bonus / admin
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # баланс после операции (для удобного отображения истории)
    balance_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
