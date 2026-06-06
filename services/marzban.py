"""Клиент Marzban API. Бот обращается к Marzban на VPS только по API:
создание/продление/бан ключа и чтение данных (HWID/IP/трафик).

Одна подписка = один Marzban-юзер = один VLESS-ключ.
Лимит одновременных устройств задаётся через поле ip_limit (доп-модуль Marzban IP-Limit).
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from config import config

log = logging.getLogger(__name__)


class MarzbanError(Exception):
    pass


def _demo_vless(username: str) -> str:
    """Демо-ключ для теста без реального Marzban/VPS (не работает как VPN)."""
    return (
        "vless://00000000-0000-0000-0000-000000000000@demo.feervpn.local:443"
        "?type=tcp&security=reality&sni=example.com&fp=chrome"
        f"#FeerVPN-TEST-{username}"
    )


class MarzbanClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_exp: float = 0.0

    @property
    def enabled(self) -> bool:
        """Marzban настроен только если задан базовый URL (иначе — тестовый режим)."""
        return bool(config.MARZBAN_BASE_URL)

    # ---------- внутреннее ----------
    async def _get_token(self, session: aiohttp.ClientSession) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        url = f"{config.MARZBAN_BASE_URL}/api/admin/token"
        data = {
            "username": config.MARZBAN_USERNAME,
            "password": config.MARZBAN_PASSWORD,
        }
        async with session.post(url, data=data) as r:
            if r.status != 200:
                raise MarzbanError(f"Авторизация Marzban не удалась: {r.status}")
            payload = await r.json()
        self._token = payload["access_token"]
        # токен Marzban живёт по умолчанию ~24ч, обновляем раньше
        self._token_exp = time.time() + 3600
        return self._token

    async def _request(
        self, method: str, path: str, json: dict | None = None
    ) -> Any:
        async with aiohttp.ClientSession() as session:
            token = await self._get_token(session)
            headers = {"Authorization": f"Bearer {token}"}
            url = f"{config.MARZBAN_BASE_URL}{path}"
            async with session.request(method, url, json=json, headers=headers) as r:
                text = await r.text()
                if r.status >= 400:
                    raise MarzbanError(f"{method} {path} -> {r.status}: {text}")
                if not text:
                    return None
                return await r.json() if r.content_type == "application/json" else text

    # ---------- публичное ----------
    async def create_user(
        self, username: str, days: int, device_limit: int
    ) -> dict:
        """Создаёт юзера с одним VLESS-ключом и лимитом устройств (ip_limit)."""
        if not self.enabled:
            log.warning(
                "Marzban не настроен (MARZBAN_BASE_URL пуст) — тестовый режим, "
                "выдаю демо-ключ для %s",
                username,
            )
            return {"username": username, "status": "active", "_stub": True}
        expire = int((datetime.utcnow() + timedelta(days=days)).timestamp())
        body = {
            "username": username,
            "proxies": {config.MARZBAN_PROXY_PROTOCOL: {}},
            "inbounds": {config.MARZBAN_PROXY_PROTOCOL: [config.MARZBAN_INBOUND_TAG]},
            "expire": expire,
            "data_limit": 0,
            "data_limit_reset_strategy": "no_reset",
            "status": "active",
            # доп-модуль Marzban IP-Limit читает это поле из inbounds/note
            "note": f"feervpn ip_limit={device_limit}",
        }
        return await self._request("POST", "/api/user", json=body)

    async def get_user(self, username: str) -> dict:
        return await self._request("GET", f"/api/user/{username}")

    async def get_subscription_url(self, username: str) -> str:
        """Подписочная ссылка (subscription_url) — её клиент импортирует в приложение."""
        user = await self.get_user(username)
        sub = user.get("subscription_url", "")
        if sub.startswith("/"):
            sub = f"{config.MARZBAN_BASE_URL}{sub}"
        return sub

    async def get_vless_link(self, username: str) -> str:
        """Первая прямая vless:// ссылка (для QR), fallback на subscription_url."""
        if not self.enabled:
            return _demo_vless(username)
        user = await self.get_user(username)
        links = user.get("links") or []
        for link in links:
            if link.startswith("vless://"):
                return link
        return await self.get_subscription_url(username)

    async def renew(self, username: str, days: int) -> dict:
        """Сдвигает дату окончания от текущего expire (или от сейчас)."""
        if not self.enabled:
            return {"username": username, "_stub": True}
        user = await self.get_user(username)
        now = int(time.time())
        current = user.get("expire") or now
        base = current if current > now else now
        new_expire = base + days * 86400
        return await self._request(
            "PUT", f"/api/user/{username}", json={"expire": new_expire, "status": "active"}
        )

    async def set_status(self, username: str, status: str) -> dict:
        """status: active | disabled."""
        if not self.enabled:
            return {"username": username, "status": status, "_stub": True}
        return await self._request(
            "PUT", f"/api/user/{username}", json={"status": status}
        )

    async def ban(self, username: str) -> dict:
        return await self.set_status(username, "disabled")

    async def unban(self, username: str) -> dict:
        return await self.set_status(username, "active")

    async def delete_user(self, username: str) -> None:
        await self._request("DELETE", f"/api/user/{username}")

    async def revoke_sub(self, username: str) -> dict:
        """Перевыпуск ключа (старая ссылка перестаёт работать)."""
        return await self._request("POST", f"/api/user/{username}/revoke_sub")

    async def get_active_ips(self, username: str) -> list[str]:
        """Активные IP по ключу (из доп-модуля IP-Limit / node usage).

        Разные сборки Marzban отдают это по-разному; обрабатываем мягко.
        """
        if not self.enabled:
            return []
        try:
            data = await self._request("GET", f"/api/user/{username}/ips")
        except MarzbanError:
            return []
        if isinstance(data, dict):
            ips = data.get("ips") or data.get("active_ips") or []
            return list(ips) if isinstance(ips, list) else list(ips.keys())
        if isinstance(data, list):
            return data
        return []


marzban = MarzbanClient()
