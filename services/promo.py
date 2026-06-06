"""Система промокодов: процент / фикс / бонусные дни."""
from dataclasses import dataclass
from datetime import datetime

from db import repo
from db.models import Promocode


@dataclass
class PromoResult:
    ok: bool
    message: str
    promo: Promocode | None = None
    # пересчёт цены/дней
    new_price: int | None = None
    bonus_days: int = 0


async def validate_and_apply(
    code: str, user_id: int, base_price: int
) -> PromoResult:
    """Проверяет промокод и считает итоговую цену / бонусные дни.

    Саму редемпцию (used_count += 1) нужно вызывать ПОСЛЕ успешной оплаты
    через redeem().
    """
    promo = await repo.get_promocode(code)
    if not promo or not promo.active:
        return PromoResult(False, "Промокод не найден или отключён.")

    if promo.expires_at and promo.expires_at < datetime.utcnow():
        return PromoResult(False, "Срок действия промокода истёк.")

    if promo.usage_limit and promo.used_count >= promo.usage_limit:
        return PromoResult(False, "Лимит активаций промокода исчерпан.")

    if await repo.user_used_promo(promo.id, user_id):
        return PromoResult(False, "Ты уже использовал этот промокод.")

    if promo.only_new and await repo.user_has_any_subscription(user_id):
        return PromoResult(False, "Промокод только для новых клиентов.")

    new_price = base_price
    bonus_days = 0

    if promo.type == "percent":
        new_price = max(1, round(base_price * (1 - promo.value / 100)))
        msg = f"⇓ Скидка {int(promo.value)}% — итого {new_price} ₽"
    elif promo.type == "fixed":
        new_price = max(1, base_price - int(promo.value))
        msg = f"⇓ −{int(promo.value)} ₽ — итого {new_price} ₽"
    elif promo.type == "bonus_days":
        bonus_days = int(promo.value)
        msg = f"🎁 +{bonus_days} дней к подписке"
    else:
        return PromoResult(False, "Неизвестный тип промокода.")

    return PromoResult(
        True, msg, promo=promo, new_price=new_price, bonus_days=bonus_days
    )


async def redeem(promo_id: int, user_id: int) -> None:
    await repo.redeem_promocode(promo_id, user_id)
