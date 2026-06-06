"""Все inline-клавиатуры. Интерфейс полностью на кнопках (текст — только для промокода)."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TARIFFS, config


def main_menu(is_admin: bool = False, has_sub: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🛍 Купить подписку", callback_data="buy")
    if has_sub:
        kb.button(text="🔑 Моя подписка", callback_data="my_sub")
    kb.button(text="🏷 Промокод", callback_data="promo")
    kb.button(text="📲 Как подключиться", callback_data="howto")
    kb.button(text="🆘 Поддержка", callback_data="support")
    if is_admin:
        kb.button(text="⚙️ Админ-панель", callback_data="admin")
    kb.adjust(1, 2, 2, 1)
    return kb.as_markup()


def tariffs_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, t in TARIFFS.items():
        kb.button(
            text=f"{t['emoji']} {t['title']} — {t['price']} ₽ ({t['desc']})",
            callback_data=f"plan:{code}",
        )
    kb.button(text="« Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def buy_confirm(plan: str, price: int, promo_applied: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"💳 Оплатить {price} ₽", callback_data=f"pay:{plan}")
    if not promo_applied:
        kb.button(text="🏷 Ввести промокод", callback_data=f"promo_for:{plan}")
    kb.button(text="« Назад", callback_data="buy")
    kb.adjust(1)
    return kb.as_markup()


def payment_check(payment_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if config.DA_DONATION_URL:
        kb.button(text="🔗 Открыть страницу оплаты", url=config.DA_DONATION_URL)
    kb.button(text="✅ Я оплатил", callback_data=f"check_pay:{payment_id}")
    kb.button(text="❌ Отменить", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def my_sub_menu(banned: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not banned:
        kb.button(text="🔄 Продлить", callback_data="buy")
        kb.button(text="📱 Мои устройства", callback_data="devices")
        kb.button(text="📲 Как подключиться", callback_data="howto")
    kb.button(text="« В меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def banned_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🔓 Разбан — {config.PRICE_UNBAN} ₽ (+ Solo)", callback_data="unban")
    kb.button(text="🆘 Написать в поддержку", callback_data="support")
    kb.adjust(1)
    return kb.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="« В меню", callback_data="menu")
    return kb.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="menu")
    return kb.as_markup()


# ---------------- Админ ----------------

def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="👥 Клиенты", callback_data="adm:clients")
    kb.button(text="🏷 Промокоды", callback_data="adm:promos")
    kb.button(text="📢 Рассылка", callback_data="adm:broadcast")
    kb.button(text="🔍 Лог антифрода", callback_data="adm:fraud")
    kb.button(text="🎁 Выдать подписку", callback_data="adm:give_help")
    kb.button(text="« В меню", callback_data="menu")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def admin_promo_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать промокод", callback_data="adm:promo_new")
    kb.button(text="« Назад", callback_data="admin")
    kb.adjust(1)
    return kb.as_markup()


def admin_client_actions(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎁 Выдать Solo", callback_data=f"adm:give:{user_id}:solo")
    kb.button(text="🎁 Выдать Family", callback_data=f"adm:give:{user_id}:family")
    kb.button(text="➕ +30 дней", callback_data=f"adm:extend:{user_id}")
    kb.button(text="🚫 Бан", callback_data=f"adm:ban:{user_id}")
    kb.button(text="✅ Разбан", callback_data=f"adm:unban:{user_id}")
    kb.button(text="« Назад", callback_data="adm:clients")
    kb.adjust(2, 1, 2, 1)
    return kb.as_markup()
