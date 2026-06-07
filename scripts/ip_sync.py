#!/usr/bin/env python3
"""Feer VPN — учёт активных IP + БЛОКИРОВКА лишних устройств через iptables.

Запускается по cron НА VPS (рядом с Marzban), от root.

Что делает:
  1) Читает xray access.log, группирует IP по каждому ключу (marzban username)
     за окно ENFORCE_WINDOW_MIN минут.
  2) Пишет активные IP в таблицу devices (для отображения в боте).
  3) Если у ключа активных IP больше device_limit — ОСТАВЛЯЕТ «старшие» IP
     (те, что подключились раньше) в пределах лимита, а ЛИШНИЕ IP блокирует
     через iptables (цепочка FEER_BAN, DROP на порты VPN). У лишнего устройства
     просто перестаёт работать интернет через VPN; первое устройство не трогаем.
  4) Бан держится BAN_MINUTES минут (хранится в bans.json), потом снимается;
     если лишнее устройство подключится снова — снова бан.
  5) Шлёт владельцу ключа уведомление в Telegram (один раз на бан).

ПОЧЕМУ ИМЕННО ТАК:
  Одна подписка = один общий VLESS-ключ (один UUID). Отличить устройства можно
  только по исходному IP. Marzban API умеет только отключить весь ключ
  целиком, поэтому точечная блокировка «только второго устройства» делается
  на уровне iptables по его IP.

ЗАВИСИМОСТИ: requests (pip3 install requests), iptables (есть по умолчанию), root.
ПЕРЕМЕННЫЕ (окружение или .env рядом со скриптом):
    TURSO_DATABASE_URL=libsql://...turso.io
    TURSO_AUTH_TOKEN=...
    ACCESS_LOG=/var/lib/marzban/access.log
    IP_WINDOW_MIN=10            # окно для отображения в боте
    ENFORCE_WINDOW_MIN=5        # окно для решения о блокировке
    ENFORCE=1                   # 1=блокировать, 0=только учёт
    BAN_MINUTES=30              # на сколько блокируем лишний IP
    VPN_PORTS=80,443,8443       # порты VPN, которые режем (SSH 22 НЕ трогаем)
    BAN_STATE=/opt/feer-ip/bans.json
    BOT_TOKEN=...               # для уведомлений (опционально)
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


def _load_env() -> None:
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
ENFORCE_WINDOW_MIN = int(os.environ.get("ENFORCE_WINDOW_MIN", "5"))
ENFORCE = os.environ.get("ENFORCE", "1").strip().lower() in ("1", "true", "yes")
BAN_MINUTES = int(os.environ.get("BAN_MINUTES", "30"))
VPN_PORTS = os.environ.get("VPN_PORTS", "80,443,8443").replace(" ", "")
BAN_STATE = os.environ.get("BAN_STATE", str(Path(__file__).resolve().parent / "bans.json"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SEEN_STATE = os.environ.get("SEEN_STATE", str(Path(BAN_STATE).parent / "seen.json"))
ADMIN_IDS = set()
for _x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(","):
    if _x.isdigit():
        ADMIN_IDS.add(int(_x))

_raw_url = os.environ.get("TURSO_DATABASE_URL", "")
DB_URL = _raw_url.replace("libsql://", "https://").replace("wss://", "https://").rstrip("/")
TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

if not DB_URL or not TOKEN:
    sys.exit("❌ Нет TURSO_DATABASE_URL / TURSO_AUTH_TOKEN (в окружении или .env)")

LINE_RE = re.compile(
    r"(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})"
    r".*?(?P<ip>\d{1,3}(?:\.\d{1,3}){3}):\d+"
    r".*?email:\s*(?P<email>\S+)"
)


# ---------- Turso ----------
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
    reqs = [
        {"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in args]}}
        for sql, args in statements
    ]
    reqs.append({"type": "close"})
    resp = requests.post(
        DB_URL + "/v2/pipeline",
        headers={"Authorization": "Bearer " + TOKEN},
        json={"requests": reqs},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _rows(result):
    if result.get("type") != "ok":
        return []
    r = result.get("response", {}).get("result", {})
    return [[cell.get("value") for cell in row] for row in r.get("rows", [])]


# ---------- Telegram ----------
def tg_send(tg_id, text):
    if not BOT_TOKEN or not tg_id:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage",
            json={"chat_id": int(tg_id), "text": text},
            timeout=15,
        )
    except Exception as e:
        print("⚠️ TG-уведомление не отправлено: " + str(e))


# ---------- iptables ----------
def _ipt(args):
    return subprocess.run(["iptables"] + args, capture_output=True, text=True)


def ensure_chain():
    """Создаём цепочку FEER_BAN и подвешиваем её в INPUT (и DOCKER-USER если есть)."""
    if _ipt(["-nL", "FEER_BAN"]).returncode != 0:
        _ipt(["-N", "FEER_BAN"])
    if _ipt(["-C", "INPUT", "-j", "FEER_BAN"]).returncode != 0:
        _ipt(["-I", "INPUT", "-j", "FEER_BAN"])
    if _ipt(["-nL", "DOCKER-USER"]).returncode == 0:
        if _ipt(["-C", "DOCKER-USER", "-j", "FEER_BAN"]).returncode != 0:
            _ipt(["-I", "DOCKER-USER", "-j", "FEER_BAN"])


def _rule(ip):
    return ["FEER_BAN", "-s", ip, "-p", "tcp", "-m", "multiport", "--dports", VPN_PORTS, "-j", "DROP"]


def ban_ip(ip):
    if _ipt(["-C"] + _rule(ip)).returncode != 0:
        _ipt(["-A"] + _rule(ip))


def unban_ip(ip):
    # удаляем все дубли правила
    while _ipt(["-C"] + _rule(ip)).returncode == 0:
        _ipt(["-D"] + _rule(ip))


def load_bans():
    try:
        with open(BAN_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_bans(bans):
    try:
        Path(BAN_STATE).parent.mkdir(parents=True, exist_ok=True)
        with open(BAN_STATE, "w", encoding="utf-8") as f:
            json.dump(bans, f)
    except Exception as e:
        print("⚠️ Не могу сохранить " + BAN_STATE + ": " + str(e))


def load_seen():
    """Устойчивое время первого появления IP: {username: {ip: epoch}}."""
    try:
        with open(SEEN_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen(seen):
    try:
        Path(SEEN_STATE).parent.mkdir(parents=True, exist_ok=True)
        with open(SEEN_STATE, "w", encoding="utf-8") as f:
            json.dump(seen, f)
    except Exception as e:
        print("⚠️ Не могу сохранить " + SEEN_STATE + ": " + str(e))


# ---------- разбор лога ----------
def parse_lines():
    if not os.path.exists(ACCESS_LOG):
        sys.exit("❌ Нет файла лога: " + ACCESS_LOG + " (включи access-лог в xray_config.json)")
    try:
        with open(ACCESS_LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-8000:]
    except OSError as e:
        sys.exit("❌ Не могу прочитать " + ACCESS_LOG + ": " + str(e))
    parsed = []
    for line in lines:
        m = LINE_RE.search(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
        parsed.append((ts, m.group("email").strip(), m.group("ip")))
    newest = max((ts for ts, _, _ in parsed), default=None)
    return parsed, newest


def build_active(parsed, newest, minutes):
    """-> {email: {ip: (first_ts, last_ts)}} за окно minutes от последней записи."""
    data = {}
    if not parsed or newest is None:
        return data
    cutoff = newest - timedelta(minutes=minutes)
    for ts, email, ip in parsed:
        if ts < cutoff:
            continue
        bucket = data.setdefault(email, {})
        if ip not in bucket:
            bucket[ip] = (ts, ts)
        else:
            first_ts, last_ts = bucket[ip]
            bucket[ip] = (min(first_ts, ts), max(last_ts, ts))
    return data


def _norm(name):
    return re.sub(r"^\d+\.", "", name)


def load_subs():
    """-> dict username -> {id, limit, status, user_id, tg_id}."""
    res = execute([
        (
            "SELECT s.id, s.marzban_username, s.device_limit, s.status, s.user_id, u.tg_id "
            "FROM subscriptions s JOIN users u ON u.id = s.user_id "
            "WHERE s.marzban_username IS NOT NULL",
            [],
        ),
    ])
    out = {}
    for row in _rows(res[0]):
        sid, uname, limit, status, user_id, tg_id = row
        if not uname:
            continue
        out[str(uname)] = {
            "id": int(sid),
            "limit": int(limit or 1),
            "status": status or "",
            "user_id": int(user_id),
            "tg_id": int(tg_id) if tg_id is not None else None,
        }
    return out


def sync_devices(data, sub_map):
    """Запись активных IP в devices (для отображения в боте)."""
    total = 0
    for email, ips in data.items():
        sub = sub_map.get(email) or sub_map.get(_norm(email))
        if not sub:
            continue
        sid = sub["id"]
        for ip, (first_ts, last_ts) in ips.items():
            seen = last_ts.strftime("%Y-%m-%d %H:%M:%S")
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
    return total


def mark_blocked(sid, ip):
    execute([
        ("UPDATE devices SET status = 'blocked' WHERE subscription_id = ? AND last_ip = ?", [sid, ip]),
    ])


def enforce(enforce_data, sub_map):
    """Блокируем лишние IP. Порядок «кто раньше» берём из устойчивого
    state (seen.json), а НЕ из окна лога — поэтому ПЕРВОЕ устройство не банится никогда."""
    ensure_chain()
    bans = load_bans()
    seen = load_seen()
    now = time.time()
    offenders = {}  # ip -> {username, tg_id, sid}

    # 1) собираем активные IP по каждому ключу + самую раннюю метку из лога
    active_by_user = {}  # username -> [sub, {ip: log_first_epoch}]
    for email, ips in enforce_data.items():
        sub = sub_map.get(email) or sub_map.get(_norm(email))
        if not sub:
            continue
        uname = email if email in sub_map else _norm(email)
        cur = active_by_user.setdefault(uname, [sub, {}])
        for ip, ts_pair in ips.items():
            e = ts_pair[0].timestamp()
            if ip not in cur[1] or e < cur[1][ip]:
                cur[1][ip] = e

    for uname, pair in active_by_user.items():
        sub, ip_first = pair
        # админов не трогаем вообще
        if sub.get("tg_id") in ADMIN_IDS:
            seen.pop(uname, None)
            continue

        active_ips = set(ip_first.keys())
        user_seen = seen.setdefault(uname, {})
        # фиксируем время первого появления IP (один раз, дальше не меняется)
        for ip in active_ips:
            if ip not in user_seen:
                user_seen[ip] = ip_first[ip]
        # убираем IP, которых сейчас нет в эфире — освобождаем слот
        for ip in list(user_seen.keys()):
            if ip not in active_ips:
                del user_seen[ip]

        limit = sub["limit"]
        if len(active_ips) <= limit:
            continue

        # сортируем по устойчивому времени первого подключения: ранние = «свои»
        ordered = sorted(active_ips, key=lambda ip: (user_seen.get(ip, now), ip))
        keep = ordered[:limit]
        extra = ordered[limit:]
        print("🚫 " + uname + ": IP " + str(len(active_ips)) + "/" + str(limit)
              + " | оставляем " + ",".join(keep) + " | блок " + ",".join(extra))
        for ip in extra:
            offenders[ip] = {"username": uname, "tg_id": sub["tg_id"], "sid": sub["id"]}

    # 2) баним / продлеваем лишние IP
    for ip, info in offenders.items():
        new_ban = ip not in bans
        ban_ip(ip)
        bans[ip] = {"until": now + BAN_MINUTES * 60, "username": info["username"]}
        mark_blocked(info["sid"], ip)
        if new_ban:
            tg_send(
                info["tg_id"],
                "⚠️ Лишнее устройство отключено\n\n"
                "Обнаружено подключение сверх лимита твоего тарифа. "
                "Доступ для лишнего устройства временно заблокирован. "
                "Нужно больше устройств — оформи тариф «Семья».",
            )

    # 3) снимаем истёкшие баны; остальные держим и гарантируем правило
    for ip in list(bans.keys()):
        if ip in offenders:
            continue
        if now >= bans[ip].get("until", 0):
            unban_ip(ip)
            del bans[ip]
        else:
            ban_ip(ip)  # самовосстановление после перезагрузки / flush

    save_bans(bans)
    save_seen(seen)
    return len(offenders), len(bans)


def main():
    parsed, newest = parse_lines()
    if not parsed:
        print("ℹ️ Подходящих строк в логе не найдено.")
        return

    sub_map = load_subs()
    if not sub_map:
        print("ℹ️ Нет подписок с marzban_username.")
        return

    data = build_active(parsed, newest, WINDOW_MIN)
    synced = sync_devices(data, sub_map)

    blocked = held = 0
    if ENFORCE:
        enforce_data = build_active(parsed, newest, ENFORCE_WINDOW_MIN)
        blocked, held = enforce(enforce_data, sub_map)

    print("✅ Записей: " + str(synced) + " | лишних IP сейчас: " + str(blocked)
          + " | всего в бане: " + str(held) + " | юзеров с IP: " + str(len(data)))


if __name__ == "__main__":
    main()
