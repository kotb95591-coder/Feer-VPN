#!/usr/bin/env python3
"""Feer VPN — синхронизация активных IP-устройств из логов xray в Turso.

Запускается по cron НА VPS (рядом с Marzban). Читает access.log xray,
собирает IP клиентов по каждому marzban-юзеру за последние N минут
и пишет их в таблицу devices базы Turso (ту же, что читает бот).

Зависимости: requests (pip3 install requests).
Переменные окружения (или файл .env рядом со скриптом):
    TURSO_DATABASE_URL=libsql://...turso.io
    TURSO_AUTH_TOKEN=...
    ACCESS_LOG=/var/lib/marzban/access.log   (опционально)
    IP_WINDOW_MIN=10                          (окно активности, мин)
"""
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


def _load_env() -> None:
    """Подхватить .env рядом со скриптом, если есть."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

ACCESS_LOG = os.environ.get("ACCESS_LOG", "/var/lib/marzban/access.log")
WINDOW_MIN = int(os.environ.get("IP_WINDOW_MIN", "10"))

_raw_url = os.environ.get("TURSO_DATABASE_URL", "")
DB_URL = _raw_url.replace("libsql://", "https://").replace("wss://", "https://").rstrip("/")
TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

if not DB_URL or not TOKEN:
    sys.exit("❌ Нет TURSO_DATABASE_URL / TURSO_AUTH_TOKEN (в окружении или .env)")

# Строка access.log xray: берём время, первый IP:port (клиент) и email.
LINE_RE = re.compile(
    r"(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})"
    r".*?(?P<ip>\d{1,3}(?:\.\d{1,3}){3}):\d+"
    r".*?email:\s*(?P<email>\S+)"
)


def _arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def execute(statements):
    """statements: list[(sql, [args])]. Возвращает list сырых result-объектов."""
    reqs = [
        {"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in args]}}
        for sql, args in statements
    ]
    reqs.append({"type": "close"})
    resp = requests.post(
        f"{DB_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"requests": reqs},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _rows(result):
    """Извлечь rows из result-объекта pipeline."""
    if result.get("type") != "ok":
        return []
    r = result.get("response", {}).get("result", {})
    out = []
    for row in r.get("rows", []):
        out.append([cell.get("value") for cell in row])
    return out


def parse_log():
    """-> dict {email: {ip: last_seen_dt}} за окно WINDOW_MIN."""
    if not os.path.exists(ACCESS_LOG):
        sys.exit(f"❌ Нет файла лога: {ACCESS_LOG} (включи access-лог в xray_config.json)")
    cutoff = datetime.now() - timedelta(minutes=WINDOW_MIN)
    data = {}
    try:
        with open(ACCESS_LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-5000:]
    except OSError as e:
        sys.exit(f"❌ Не могу прочитать {ACCESS_LOG}: {e}")
    for line in lines:
        m = LINE_RE.search(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
        if ts < cutoff:
            continue
        email = m.group("email").strip()
        ip = m.group("ip")
        bucket = data.setdefault(email, {})
        if ip not in bucket or ts > bucket[ip]:
            bucket[ip] = ts
    return data


def main():
    data = parse_log()
    if not data:
        print("ℹ️ Активных подключений в окне не найдено.")
        return
    res = execute([
        ("SELECT id, marzban_username FROM subscriptions WHERE marzban_username IS NOT NULL", []),
    ])
    sub_map = {}
    for row in _rows(res[0]):
        sid, uname = row[0], row[1]
        if uname:
            sub_map[str(uname)] = int(sid)

    total = 0
    for email, ips in data.items():
        sid = sub_map.get(email)
        if not sid:
            continue
        for ip, ts in ips.items():
            seen = ts.strftime("%Y-%m-%d %H:%M:%S")
            chk = execute([
                ("SELECT id FROM devices WHERE subscription_id = ? AND last_ip = ?", [sid, ip]),
            ])
            found = _rows(chk[0])
            if found:
                execute([
                    ("UPDATE devices SET last_seen = ?, status = 'active' WHERE id = ?", [seen, int(found[0][0])]),
                ])
            else:
                execute([
                    (
                        "INSERT INTO devices (subscription_id, hwid, first_ip, last_ip, last_seen, status, created_at) "
                        "VALUES (?, NULL, ?, ?, ?, 'active', ?)",
                        [sid, ip, ip, seen, seen],
                    ),
                ])
            total += 1
    print(f"✅ Синхронизировано записей: {total} (юзеров с IP: {len(data)})")


if __name__ == "__main__":
    main()
