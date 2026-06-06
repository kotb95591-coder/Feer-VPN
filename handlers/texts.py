"""Тексты сообщений бота."""
from config import TARIFFS, config


def welcome(name: str) -> str:
    return (
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Это <b>Feer VPN</b> — быстрый и стабильный VPN на основе VLESS.\n"
        "Выбери действие ниже 👇"
    )


def main_menu_text() -> str:
    return (
        "🏠 <b>Главное меню</b>\n\n"
        f"• {TARIFFS['solo']['emoji']} <b>Solo</b> — {TARIFFS['solo']['price']} ₽/мес · 1 устройство\n"
        f"• {TARIFFS['family']['emoji']} <b>Семья</b> — {TARIFFS['family']['price']} ₽/мес · до {TARIFFS['family']['devices']} устройств"
    )


def tariffs_text() -> str:
    return (
        "🛍 <b>Выбери тариф</b>\n\n"
        f"{TARIFFS['solo']['emoji']} <b>Solo</b> — {TARIFFS['solo']['price']} ₽/мес\n"
        "Для одного устройства.\n\n"
        f"{TARIFFS['family']['emoji']} <b>Семья</b> — {TARIFFS['family']['price']} ₽/мес\n"
        f"Один ключ на всю семью — до {TARIFFS['family']['devices']} устройств.\n\n"
        "⚠️ Одна подписка = один ключ. Расшаривание сверх лимита ведёт к бану."
    )


def howto_text() -> str:
    return (
        "📲 <b>Как подключиться</b>\n\n"
        "1️⃣ Установи приложение:\n"
        "• <b>Android / Windows</b>: v2rayTun или Happ\n"
        "• <b>iOS / macOS</b>: Happ или Streisand\n\n"
        "2️⃣ Открой «Моя подписка» и скопируй ключ или отсканируй QR.\n"
        "3️⃣ Вставь ключ в приложение и нажми «Подключиться».\n\n"
        "Готово! Интернет теперь защищён 🔒"
    )


def support_text() -> str:
    return (
        "🆘 <b>Поддержка</b>\n\n"
        "Напиши свой вопрос одним сообщением — проблема с оплатой, подключением или разбаном.\n"
        "Мы получим его и ответим как можно быстрее 👇"
    )


def banned_text(reason: str | None) -> str:
    return (
        "🚫 <b>Аккаунт заблокирован</b>\n\n"
        f"Причина: {reason or 'нарушение правил'}.\n\n"
        f"Разблокировка стоит <b>{config.PRICE_UNBAN} ₽</b> и включает подписку Solo на месяц.\n"
        "Или обратись в поддержку."
    )
