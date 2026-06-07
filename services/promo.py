"""Система промокодов: комбинируемые скидка % / спец-цена / бонусные дни.

Промокод может одновременно содержать:
- percent      — скидка в процентах;
- fixed_price  — спец-цена (₽), перекрывающая базовую;
- bonus_days   — бонусные дни к подписке;
- target_plan  — для какого тарифа действует (all / solo / family).

Поддерживаются и старые промокоды (поля type/value).
"""
from dataclasses import dataclass
from datetime import datetime

from db import repo
from db.models import Promocode

_PLAN_LABEL = {"all": "всех тарифов", "solo": "тарифа Solo", "family": "тарифа Семья"}


@dataclass
class PromoResult:
    ok: bool
    message: str
    promo: Promocode | None = None
    # пересчёт цены/дней
    new_price: int | None = None
    bonus_days: int = 0


def _legacy_effect(promo: Promocode, base_price: int):
    """Старый промокод: один эффект по type/value."""
    if promo.type == "percent":
        new_price = max(1, round(base_price * (1 - promo.value / 100)))
        return new_price, 0, f"⇓ Скидка {int(promo.value)}% — итого {new_price} ₽"
    if promo.type == "fixed":
        new_price = max(1, base_price - int(promo.value))
        return new_price, 0, f"⇓ −{int(promo.value)} ₽ — итого {new_price} ₽"
    if promo.type == "bonus_days":
        return base_price, int(promo.value), f"🎁 +{int(promo.value)} дней к подписке"
    return base_price, 0, "Промокод применён."


async def validate_and_apply(
    code: str, user_id: int, base_price: int, plan: str | None = None
) -> PromoResult:
    """Проверяет промокод и считает итоговую цену / бонусные дни.

    Редемпцию (used_count += 1) нужно вызывать ПОСЛЕ успешной оплаты через redeem().
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

    target = getattr(promo, "target_plan", "all") or "all"
    if plan and target != "all" and target != plan:
        return PromoResult(
            False, f"Промокод действует только для {_PLAN_LABEL.get(target, target)}."
        )

    percent = float(getattr(promo, "percent", 0) or 0)
    fixed = float(getattr(promo, "fixed_price", 0) or 0)
    bonus = int(getattr(promo, "bonus_days", 0) or 0)

    # Нет combo-полей — это старый промокод
    if percent == 0 and fixed == 0 and bonus == 0:
        new_price, bonus_days, msg = _legacy_effect(promo, base_price)
        return PromoResult(
            True, msg, promo=promo, new_price=new_price, bonus_days=bonus_days
        )

    new_price = base_price
    parts = []
    if fixed > 0:
        new_price = int(fixed)
        parts.append(f"спец-цена {int(fixed)} ₽")
    if percent > 0:
        new_price = max(1, round(new_price * (1 - percent / 100)))
        parts.append(f"скидка {int(percent)}%")
    new_price = max(1, int(new_price))

    lines = []
    if parts:
        lines.append(f"⇓ {' · '.join(parts)} — итого {new_price} ₽")
    if bonus > 0:
        lines.append(f"🎁 +{bonus} дней к подписке")
    msg = "\n".join(lines) if lines else "Промокод применён."

    return PromoResult(True, msg, promo=promo, new_price=new_price, bonus_days=bonus)


async def redeem(promo_id: int, user_id: int) -> None:
    await repo.redeem_promocode(promo_id, user_id)
