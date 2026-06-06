"""Вспомогательные функции: коды оплаты, форматирование дат и т.п."""
import random
import secrets
import string
from datetime import datetime


def gen_payment_code(prefix: str = "FEER") -> str:
    """Уникальный код для сообщения доната, напр. FEER-7K2Q9."""
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(5))
    return f"{prefix}-{suffix}"


def gen_marzban_username(tg_id: int) -> str:
    """Имя юзера в Marzban: feer_<tg_id>_<rnd>."""
    rnd = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"feer_{tg_id}_{rnd}"


def gen_referral_code(tg_id: int) -> str:
    return f"REF{tg_id}"


def fmt_date(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d.%m.%Y")


def fmt_datetime(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d.%m.%Y %H:%M")


def days_left(expires_at: datetime | None) -> int:
    if not expires_at:
        return 0
    delta = expires_at - datetime.utcnow()
    return max(0, delta.days)


def plan_title(plan: str) -> str:
    return {"solo": "Solo", "family": "Семья"}.get(plan, plan)
