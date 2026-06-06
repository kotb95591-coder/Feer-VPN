# Feer VPN — Telegram-бот

Продажа и выдача VPN-подписок (VLESS через Marzban) с автопроверкой оплаты, антифродом и админ-панелью. Интерфейс полностью на кнопках.

## Архитектура

```
Пользователь — Telegram
        │
        ▼
  Бот (Vercel, webhook + Cron)      ←─ хостинг без карты
        │           │
        │           └─► Turso (облачный SQLite) — БД
        │
        └─► Marzban API на VPS — выдача/бан ключей, чтение HWID/IP
                (VPS = только VPN-нода, 512 MB / 1 ядро)
        │
        └─► DonationAlerts API — автопроверка оплаты по коду
```

БД и бот вынесены с VPS, чтобы не нагружать его. VPS работает только как VPN.

## Структура

```
bot.py                — точка входа (polling или webhook)
config.py             — конфигурация из .env + тарифы
requirements.txt
vercel.json           — конфиг деплоя Vercel + Cron
.env.example

api/
  webhook.py          — serverless entrypoint Telegram webhook (Vercel)
  cron.py             — serverless entrypoint фоновых задач (Vercel Cron)

db/
  base.py             — движок Turso/libSQL + init_db
  models.py           — User, Subscription, Device, ConnectionLog, Payment, Promocode
  repo.py             — асинхронный слой доступа к данным

handlers/             — start/меню, покупка, оплата, подписка, промо, админ
keyboards/inline.py   — все inline-клавиатуры
services/
  marzban.py          — клиент Marzban API
  donationalerts.py   — автопроверка донатов
  subscription.py     — выдача/продление подписки (одна = один ключ)
  promo.py            — промокоды
  antifraud.py        — антифрод + баны
  scheduler.py        — фоновые задачи (APScheduler / Vercel Cron)
utils/
  helpers.py, qr.py
```

## Тарифы и правила

| Тариф | Цена | Устройств |
|-------|------|-----------|
| Solo  | 100 ₽/мес | 1 |
| Семья | 249 ₽/мес | до 3 (один ключ) |

**Одна подписка = один VLESS-ключ.** Семья — тот же ключ на до 3 устройствах.

### Антифрод
- Превышение лимита устройств → лишнее устройство банится по HWID. Если нельзя определить лишнее — банится вся подписка (= 1 нарушение).
- > 10 разных пользователей/IP на подписке → бан подписки (= 1 нарушение).
- 2 нарушения → бан аккаунта. Разбан: 300 ₽ (включает Solo на месяц) или через поддержку.

## Быстрый старт (локально, polling)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env         # заполнить значения
python bot.py
```

Без `TURSO_DATABASE_URL` бот использует локальный `feervpn.db` (удобно для тестов).

## Настройка Turso

```bash
curl -sSfL https://get.tur.so/install.sh | bash
turso auth signup
turso db create feervpn
turso db show feervpn --url          # → TURSO_DATABASE_URL
turso db tokens create feervpn       # → TURSO_AUTH_TOKEN
```

Таблицы создаются автоматически при первом запуске (`init_db`).

## Деплой на Vercel (основной вариант, без карты)

1. Залей репозиторий на GitHub и импортируй проект в Vercel.
2. В Project Settings → Environment Variables добавь все переменные из `.env.example`.
   Обязательно: `USE_WEBHOOK=true`, `WEBHOOK_BASE_URL=https://<проект>.vercel.app`, `WEBHOOK_SECRET`, `CRON_SECRET`.
3. После деплоя установи webhook Telegram:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<проект>.vercel.app/api/webhook&secret_token=<WEBHOOK_SECRET>"
```

4. Cron уже настроен в `vercel.json` (напоминания, истечение, антифрод, таймаут платежей).

> Важно: на serverless бот работает только в webhook-режиме (постоянный polling невозможен). Фоновые задачи — через Vercel Cron.

### Альтернативы хостинга
- **Cloudflare Workers** — аналогично webhook + Cron Triggers.
- **Render** — можно запустить long-running `python bot.py` (polling или webhook) + APScheduler.
- **VPS** — тот же `python bot.py`; планировщик запускается автоматически.

## Marzban (на VPS)

- Установи Marzban, создай inbound VLESS (Reality) и укажи его тег в `MARZBAN_INBOUND_TAG`.
- Для лимита устройств используй доп-модуль Marzban IP-Limit — бот пишет лимит в поле `note` юзера.
- Антифрод периодически читает активные IP по ключу (`/api/user/<username>/ips`).

## DonationAlerts

Бот работает с OAuth-токеном и умеет **сам его обновлять** (вечный доступ). Токены хранятся в БД (таблица `settings`), при 401 access-токен обновляется через `refresh_token`.

### Быстрый вариант (токен на год)
1. Создай приложение на https://www.donationalerts.com/application/clients (Redirect URL — `https://localhost`).
2. Открой в браузере (implicit flow), подставив свой client_id:
   ```
   https://www.donationalerts.com/oauth/authorize?client_id=CLIENT_ID&redirect_uri=https://localhost&response_type=token&scope=oauth-user-show%20oauth-donation-index%20oauth-donation-subscribe
   ```
3. Скопируй `access_token` из адресной строки (после `#access_token=`) → `DA_ACCESS_TOKEN`. Живёт 1 год.

### Вечный вариант (авто-обновление — рекомендуется)
Для авто-обновления нужен `refresh_token` (его даёт только Authorization Code flow):
1. В `.env` заполни `DA_CLIENT_ID` и `DA_CLIENT_SECRET` (из карточки приложения: «ID приложения» и «Ключ API»).
2. Открой в браузере (обрати внимание: `response_type=code`):
   ```
   https://www.donationalerts.com/oauth/authorize?client_id=CLIENT_ID&redirect_uri=https://localhost&response_type=code&scope=oauth-user-show%20oauth-donation-index%20oauth-donation-subscribe
   ```
3. После «Разрешить» скопируй `code` из адреса (`https://localhost/?code=...`).
4. Обменяй код на токены:
   ```bash
   curl -X POST https://www.donationalerts.com/oauth/token \
     -d "grant_type=authorization_code" \
     -d "client_id=CLIENT_ID" \
     -d "client_secret=CLIENT_SECRET" \
     -d "redirect_uri=https://localhost" \
     -d "code=КОД_ИЗ_БРАУЗЕРА"
   ```
5. Из JSON-ответа возьми `refresh_token` → `DA_REFRESH_TOKEN` (и `access_token` → `DA_ACCESS_TOKEN`).

Дальше бот будет обновлять access-токен сам и сохранять в БД — возвращаться к этому больше не нужно.

### Как работает оплата
Клиент платит и вставляет выданный код (FEER-XXXXX) в сообщение доната. Бот находит донат по коду + сумме, защищено от повторного зачёта по `da_id`.

## Админ-команды

- `/client <tg_id>` — карточка клиента (бан/разбан/+30 дней).
- `/addpromo <код> <percent|fixed|bonus_days> <значение> [лимит] [new]` — создать промокод.
- Кнопка «Админ-панель»: статистика, клиенты, промокоды, рассылка, лог антифрода.

Админы задаются в `ADMIN_IDS` (через запятую).

## Клиентские приложения

Happ, v2rayTun, Streisand — импорт ключа/QR или подписочной ссылки.
