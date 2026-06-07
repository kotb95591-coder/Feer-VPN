"""Центральная конфигурация Feer VPN бота. Всё читается из .env."""
import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Отсутствует обязательная переменная окружения: {name}")
    return val or ""


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Config:
    # ---- Telegram ----
    BOT_TOKEN = _get("BOT_TOKEN", required=True)
    ADMIN_IDS = [
        int(x) for x in _get("ADMIN_IDS", "").replace(" ", "").split(",") if x
    ]

    # ---- Режим запуска ----
    USE_WEBHOOK = _get("USE_WEBHOOK", "false").lower() == "true"
    WEBHOOK_BASE_URL = _get("WEBHOOK_BASE_URL", "").rstrip("/")
    WEBHOOK_PATH = _get("WEBHOOK_PATH", "/api/webhook")
    WEBHOOK_SECRET = _get("WEBHOOK_SECRET", "")
    WEBAPP_HOST = _get("WEBAPP_HOST", "0.0.0.0")
    WEBAPP_PORT = _int("WEBAPP_PORT", 8080)
    CRON_SECRET = _get("CRON_SECRET", "")

    @property
    def WEBHOOK_URL(self) -> str:
        return f"{self.WEBHOOK_BASE_URL}{self.WEBHOOK_PATH}"

    # ---- База данных (Turso / libSQL) ----
    TURSO_DATABASE_URL = _get("TURSO_DATABASE_URL", "")
    TURSO_AUTH_TOKEN = _get("TURSO_AUTH_TOKEN", "")
    LOCAL_DB_PATH = _get("LOCAL_DB_PATH", "feervpn.db")

    # ---- Marzban ----
    MARZBAN_BASE_URL = _get("MARZBAN_BASE_URL", "").rstrip("/")
    MARZBAN_USERNAME = _get("MARZBAN_USERNAME", "")
    MARZBAN_PASSWORD = _get("MARZBAN_PASSWORD", "")
    MARZBAN_INBOUND_TAG = _get("MARZBAN_INBOUND_TAG", "VLESS WS CDN")
    MARZBAN_PROXY_PROTOCOL = _get("MARZBAN_PROXY_PROTOCOL", "vless")
    # Если панель Marzban работает по самоподписанному сертификату (без домена) — ставим false.
    MARZBAN_VERIFY_SSL = _get("MARZBAN_VERIFY_SSL", "true").lower() == "true"

    # ---- DonationAlerts ----
    DA_ACCESS_TOKEN = _get("DA_ACCESS_TOKEN", "")
    DA_API_BASE = _get("DA_API_BASE", "https://www.donationalerts.com/api/v1").rstrip("/")
    DA_DONATION_URL = _get("DA_DONATION_URL", "")
    # Для вечного доступа — авто-обновление токена
    DA_CLIENT_ID = _get("DA_CLIENT_ID", "")
    DA_CLIENT_SECRET = _get("DA_CLIENT_SECRET", "")
    DA_REFRESH_TOKEN = _get("DA_REFRESH_TOKEN", "")
    DA_SCOPES = _get(
        "DA_SCOPES",
        "oauth-user-show oauth-donation-index oauth-donation-subscribe",
    )

    # ---- Картинки / баннеры (путь к файлу, URL или Telegram file_id; пусто = без картинки) ----
    IMG_WELCOME = _get("IMG_WELCOME", "assets/welcome.png")
    IMG_TARIFFS = _get("IMG_TARIFFS", "assets/tariffs.png")
    IMG_HOWTO = _get("IMG_HOWTO", "assets/howto.png")

    # ---- Тарифы ----
    PRICE_SOLO = _int("PRICE_SOLO", 100)
    PRICE_FAMILY = _int("PRICE_FAMILY", 249)
    PRICE_UNBAN = _int("PRICE_UNBAN", 300)
    SUB_DAYS = _int("SUB_DAYS", 30)
    LIMIT_SOLO = _int("LIMIT_SOLO", 1)
    LIMIT_FAMILY = _int("LIMIT_FAMILY", 3)

    # ---- Антифрод ----
    MAX_UNIQUE_USERS = _int("MAX_UNIQUE_USERS", 10)
    VIOLATIONS_TO_BAN_ACCOUNT = _int("VIOLATIONS_TO_BAN_ACCOUNT", 2)
    ANTIFRAUD_WINDOW_HOURS = _int("ANTIFRAUD_WINDOW_HOURS", 24)

    # ---- Оплата / Баланс ----
    PAYMENT_TIMEOUT_MIN = _int("PAYMENT_TIMEOUT_MIN", 30)
    # Минимальная сумма пополнения баланса (₽)
    MIN_TOPUP = _int("MIN_TOPUP", 10)
    # Пресеты сумм пополнения (кнопки в личном кабинете)
    TOPUP_PRESETS = [
        int(x)
        for x in _get("TOPUP_PRESETS", "100,250,500,1000").replace(" ", "").split(",")
        if x.isdigit()
    ]

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.ADMIN_IDS


config = Config()

# Описание тарифов (одна подписка = один ключ)
TARIFFS = {
    "solo": {
        "code": "solo",
        "title": "Solo",
        "price": config.PRICE_SOLO,
        "devices": config.LIMIT_SOLO,
        "days": config.SUB_DAYS,
        "emoji": "👤",
        "desc": "1 устройство",
    },
    "family": {
        "code": "family",
        "title": "Семья",
        "price": config.PRICE_FAMILY,
        "devices": config.LIMIT_FAMILY,
        "days": config.SUB_DAYS,
        "emoji": "👪",
        "desc": f"до {config.LIMIT_FAMILY} устройств на 1 ключе",
    },
}


def get_tariff(code: str) -> dict | None:
    return TARIFFS.get(code)
