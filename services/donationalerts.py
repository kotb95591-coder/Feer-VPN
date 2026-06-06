"""Интеграция с DonationAlerts с авто-обновлением OAuth-токена («вечный» доступ).

Логика автопроверки оплаты:
1. Бот генерирует уникальный код (FEER-XXXXX) и сумму.
2. Клиент платит на DonationAlerts и вставляет код в сообщение.
3. Бот опрашивает /api/v1/alerts/donations и ищет донат с нужным кодом и суммой.
Защита: сверка суммы, анти-дубль по da_id, таймаут ожидания.

Токены хранятся в БД (таблица settings), чтобы переживать перезапуски и
serverless-окружение (Vercel). При 401 access_token автоматически
обновляется через refresh_token + Client ID/Secret — ручное вмешательство не нужно.
"""
import asyncio
import logging
from dataclasses import dataclass

import aiohttp

from config import config
from db import repo

log = logging.getLogger(__name__)

DA_OAUTH_TOKEN_URL = "https://www.donationalerts.com/oauth/token"
_KEY_ACCESS = "da_access_token"
_KEY_REFRESH = "da_refresh_token"


@dataclass
class Donation:
    id: str
    amount: float
    currency: str
    message: str


class DonationAlertsError(Exception):
    pass


class _TokenStore:
    """Хранит и обновляет OAuth-токены DonationAlerts.

    Источник истины — БД (settings). При первом запуске засевается из .env.
    """

    def __init__(self) -> None:
        self._access: str | None = None
        self._refresh: str | None = None
        self._loaded = False
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            db_access = await repo.get_setting(_KEY_ACCESS)
            db_refresh = await repo.get_setting(_KEY_REFRESH)
            self._access = db_access or config.DA_ACCESS_TOKEN or None
            self._refresh = db_refresh or config.DA_REFRESH_TOKEN or None
            # засеять БД из .env при первом запуске
            if self._access and not db_access:
                await repo.set_setting(_KEY_ACCESS, self._access)
            if self._refresh and not db_refresh:
                await repo.set_setting(_KEY_REFRESH, self._refresh)
            self._loaded = True

    async def access_token(self) -> str:
        await self._ensure_loaded()
        if not self._access:
            raise DonationAlertsError("DA access token не задан")
        return self._access

    async def refresh(self) -> str:
        """Обновляет access_token по refresh_token. Возвращает новый access_token."""
        await self._ensure_loaded()
        if not (self._refresh and config.DA_CLIENT_ID and config.DA_CLIENT_SECRET):
            raise DonationAlertsError(
                "Нет данных для авто-обновления токена "
                "(нужны DA_REFRESH_TOKEN, DA_CLIENT_ID, DA_CLIENT_SECRET)"
            )
        async with self._lock:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh,
                "client_id": config.DA_CLIENT_ID,
                "client_secret": config.DA_CLIENT_SECRET,
                "scope": config.DA_SCOPES,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(DA_OAUTH_TOKEN_URL, data=data) as r:
                    if r.status != 200:
                        raise DonationAlertsError(
                            f"Обновление токена не удалось ({r.status}): {await r.text()}"
                        )
                    payload = await r.json()
            new_access = payload.get("access_token")
            new_refresh = payload.get("refresh_token")
            if not new_access:
                raise DonationAlertsError("В ответе refresh нет access_token")
            self._access = new_access
            if new_refresh:
                self._refresh = new_refresh  # DonationAlerts ротирует refresh-токен
            await repo.set_setting(_KEY_ACCESS, self._access or "")
            await repo.set_setting(_KEY_REFRESH, self._refresh or "")
            log.info("DonationAlerts: access token обновлён")
            return self._access


class DonationAlertsClient:
    def __init__(self) -> None:
        self._tokens = _TokenStore()

    async def fetch_recent(self, limit: int = 50) -> list[Donation]:
        """Последние донаты через REST API (с авто-рефрешем при 401)."""
        url = f"{config.DA_API_BASE}/alerts/donations"
        payload: dict = {}
        for attempt in range(2):
            token = await self._tokens.access_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as r:
                    if r.status == 401 and attempt == 0:
                        # токен протух — обновляем и повторяем
                        await self._tokens.refresh()
                        continue
                    if r.status != 200:
                        raise DonationAlertsError(
                            f"DA API {r.status}: {await r.text()}"
                        )
                    payload = await r.json()
            break
        result: list[Donation] = []
        for item in (payload.get("data") or [])[:limit]:
            result.append(
                Donation(
                    id=str(item.get("id")),
                    amount=float(item.get("amount") or 0),
                    currency=str(item.get("currency") or "RUB"),
                    message=str(item.get("message") or ""),
                )
            )
        return result

    async def find_matching(
        self, code: str, expected_amount: float, amount_tolerance: float = 1.0
    ) -> Donation | None:
        """Ищет донат, в сообщении которого есть code и сумма >= ожидаемой."""
        donations = await self.fetch_recent()
        code_up = code.upper()
        for d in donations:
            if code_up in d.message.upper() and d.amount + amount_tolerance >= expected_amount:
                return d
        return None


donation_alerts = DonationAlertsClient()


def build_payment_instruction(code: str, amount: float) -> str:
    """Текст инструкции по оплате."""
    link = config.DA_DONATION_URL or "(ссылка не настроена)"
    return (
        f"💳 <b>Оплата {int(amount)} ₽</b>\n\n"
        f"1️⃣ Перейди по ссылке: {link}\n"
        f"2️⃣ Сумма: <b>{int(amount)} ₽</b>\n"
        f"3️⃣ В сообщении (комментарии) ОБЯЗАТЕЛЬНО укажи код:\n"
        f"<code>{code}</code>\n\n"
        f"После оплаты нажми «Я оплатил» — бот проверит и выдаст ключ автоматически."
    )
