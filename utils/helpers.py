"""Вспомогательные функции: коды оплаты, форматирование дат и т.п."""
import random
import secrets
import string
from datetime import datetime
from urllib.parse import quote


def gen_payment_code(prefix: str = "FEER") -> str:
    """Уникальный код для сообщения доната, напр. FEER-7K2Q9."""
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(5))
    return f"{prefix}-{suffix}"


def gen_marzban_username(tg_id: int) -> str:
    """Имя юзера в Marzban: feer_<tg_id>_<rnd>."""
    rnd = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"feer_{tg_id}_{rnd}"


def label_vless_link(link: str, sub_id: int) -> str:
    """Переименовывает конфиг в приложении: фрагмент после # становится 'FeerVPN (#<id>)'.

    Клиентские приложения показывают именно этот фрагмент как название подключения.
    Не vless:// ссылки (напр. subscription_url) оставляем как есть.
    """
    if not link or not link.startswith("vless://"):
        return link
    name = f"FeerVPN (#{sub_id})"
    base = link.split("#", 1)[0]
    return f"{base}#{quote(name)}"


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
