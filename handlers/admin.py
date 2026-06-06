"""Админ-панель (только для tg_id из ADMIN_IDS)."""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import TARIFFS, config
from db import repo
from handlers.states import AdminStates
from keyboards import inline
from services import subscription as sub_service
from utils.tg import edit_or_send
from services.marzban import MarzbanError, marzban
from utils.helpers import fmt_date, plan_title

log = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(tg_id: int) -> bool:
    return config.is_admin(tg_id)


@router.callback_query(F.data == "admin")
async def cb_admin(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await edit_or_send(call, "⚙️ <b>Админ-панель</b>", inline.admin_menu())
    await call.answer()


@router.callback_query(F.data == "adm:stats")
async def cb_stats(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await repo.count_users()
    active = await repo.all_active_subscriptions()
    revenue = await repo.revenue_total()
    solo = sum(1 for s in active if s.plan == "solo")
    family = sum(1 for s in active if s.plan == "family")
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: <b>{users}</b>\n"
        f"Активных подписок: <b>{len(active)}</b>\n"
        f"· Solo: {solo}\n· Семья: {family}\n\n"
        f"💰 Выручка (всего): <b>{int(revenue)} ₽</b>"
    )
    await edit_or_send(call, text, inline.admin_menu())
    await call.answer()


@router.callback_query(F.data == "adm:clients")
async def cb_clients(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await repo.list_users(limit=20)
    if not users:
        body = "Пока нет клиентов."
    else:
        lines = []
        for u in users:
            mark = "🚫" if u.status == "banned" else "✅"
            uname = f"@{u.username}" if u.username else f"id{u.tg_id}"
            lines.append(f"{mark} {uname} · tg:<code>{u.tg_id}</code> · нар.{u.violations}")
        body = "\n".join(lines)
    await edit_or_send(
        call,
        f"👥 <b>Клиенты</b> (последние 20)\n\n{body}\n\n"
        "Для действий: /client &lt;tg_id&gt;",
        inline.admin_menu(),
    )
    await call.answer()


@router.message(Command("client"))
async def cmd_client(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /client &lt;tg_id&gt;")
        return
    try:
        tg_id = int(parts[1])
    except ValueError:
        await message.answer("tg_id должен быть числом")
        return
    user = await repo.get_user(tg_id)
    if not user:
        await message.answer("Клиент не найден")
        return
    sub = await repo.get_active_subscription(user.id)
    sub_info = (
        f"Подписка: {plan_title(sub.plan)} до {fmt_date(sub.expires_at)}"
        if sub
        else "Подписка: нет"
    )
    await message.answer(
        f"👤 <b>Клиент</b> tg:<code>{user.tg_id}</code>\n"
        f"Статус: {user.status} · нарушений: {user.violations}\n"
        f"{sub_info}",
        reply_markup=inline.admin_client_actions(user.id),
    )


@router.callback_query(F.data.startswith("adm:ban:"))
async def cb_ban(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    user = await repo.get_user_by_id(user_id)
    if not user:
        await call.answer("Не найден", show_alert=True)
        return
    await repo.set_user_status(user.tg_id, "banned", "бан админом")
    for sub in user.subscriptions:
        await repo.set_subscription_status(sub.id, "banned")
        if sub.marzban_username:
            try:
                await marzban.ban(sub.marzban_username)
            except MarzbanError:
                pass
    await call.answer("Забанен", show_alert=True)


@router.callback_query(F.data.startswith("adm:unban:"))
async def cb_unban_admin(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    user = await repo.get_user_by_id(user_id)
    if not user:
        await call.answer("Не найден", show_alert=True)
        return
    await sub_service.unban_account(user)
    await call.answer("Разбанен (без выдачи ключа)", show_alert=True)


@router.callback_query(F.data.startswith("adm:extend:"))
async def cb_extend(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    user = await repo.get_user_by_id(user_id)
    sub = await repo.get_active_subscription(user_id) if user else None
    if not sub:
        await call.answer("Нет активной подписки", show_alert=True)
        return
    await repo.extend_subscription(sub.id, 30)
    if sub.marzban_username:
        try:
            await marzban.renew(sub.marzban_username, 30)
        except MarzbanError:
            pass
    await call.answer("+30 дней", show_alert=True)


# ---------------- Выдача подписки вручную ----------------

_GIVE_USAGE = (
    "🎁 <b>Выдать подписку</b>\n\n"
    "Себе:\n<code>/give solo</code> или <code>/give family 60</code>\n\n"
    "Другому (по tg_id):\n<code>/give 123456789 solo</code> или "
    "<code>/give 123456789 family 60</code>\n\n"
    f"Тарифы: {', '.join(TARIFFS.keys())}. "
    "Число в конце — срок в днях (по умолчанию — срок тарифа)."
)


@router.callback_query(F.data == "adm:give_help")
async def cb_give_help(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await edit_or_send(call, _GIVE_USAGE, inline.admin_menu())
    await call.answer()


async def _notify_granted(bot: Bot, user, plan: str, sub) -> None:
    """Отправляет получателю уведомление с ключом."""
    key = sub.vless_key or ""
    text = (
        f"🎁 <b>Вам выдана подписка {plan_title(plan)}!</b>\n"
        f"Действует до <b>{fmt_date(sub.expires_at)}</b>."
    )
    if key:
        text += f"\n\n🔑 <b>Ключ</b> (нажми, чтобы скопировать):\n<code>{key}</code>"
    try:
        await bot.send_message(user.tg_id, text)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("adm:give:"))
async def cb_give(call: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    _, _, user_id_s, plan = call.data.split(":")
    user = await repo.get_user_by_id(int(user_id_s))
    if not user:
        await call.answer("Не найден", show_alert=True)
        return
    try:
        sub, is_new = await sub_service.admin_grant(user, plan)
    except sub_service.SubscriptionError as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)
        return
    await _notify_granted(bot, user, plan, sub)
    action = "выдана" if is_new else "продлена"
    await call.answer(
        f"{plan_title(plan)} {action} до {fmt_date(sub.expires_at)}", show_alert=True
    )


@router.message(Command("give"))
async def cmd_give(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = (message.text or "").split()[1:]
    if not args:
        await message.answer(_GIVE_USAGE)
        return

    if args[0].lower() in TARIFFS:
        # выдача себе
        plan = args[0].lower()
        days_arg = args[1] if len(args) > 1 else None
        user = await repo.get_or_create_user(
            message.from_user.id, message.from_user.username
        )
    else:
        try:
            target_tg = int(args[0])
        except ValueError:
            await message.answer(
                "Первый аргумент — код тарифа (себе) или tg_id числом (другому)."
            )
            return
        if len(args) < 2 or args[1].lower() not in TARIFFS:
            await message.answer(f"Укажи тариф: {', '.join(TARIFFS.keys())}")
            return
        plan = args[1].lower()
        days_arg = args[2] if len(args) > 2 else None
        user = await repo.get_user(target_tg)
        if not user:
            await message.answer(
                "Пользователь не найден — он должен хотя бы раз запустить бота (/start)."
            )
            return

    days = None
    if days_arg is not None:
        if not days_arg.isdigit():
            await message.answer("Срок (дней) должен быть числом.")
            return
        days = int(days_arg)

    try:
        sub, is_new = await sub_service.admin_grant(user, plan, days)
    except sub_service.SubscriptionError as e:
        await message.answer(f"⚠️ Не удалось выдать: {e}")
        return

    await _notify_granted(bot, user, plan, sub)
    action = "выдана" if is_new else "продлена"
    await message.answer(
        f"✅ Подписка {plan_title(plan)} {action} пользователю "
        f"tg:<code>{user.tg_id}</code> до <b>{fmt_date(sub.expires_at)}</b>."
    )


@router.message(Command("reply"))
async def cmd_reply(message: Message, bot: Bot) -> None:
    """Ответ пользователю на сообщение в поддержку: /reply <tg_id> <текст>."""
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /reply &lt;tg_id&gt; &lt;текст&gt;")
        return
    try:
        tg_id = int(parts[1])
    except ValueError:
        await message.answer("tg_id должен быть числом")
        return
    try:
        await bot.send_message(
            tg_id,
            f"💬 <b>Ответ поддержки</b>\n\n{parts[2]}",
        )
    except Exception as e:  # noqa: BLE001
        await message.answer(f"⚠️ Не удалось отправить: {e}")
        return
    await message.answer("✅ Ответ отправлен пользователю.")


# ---------------- Промокоды ----------------

@router.callback_query(F.data == "adm:promos")
async def cb_promos(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promos = await repo.list_promocodes()
    if not promos:
        body = "Промокодов нет."
    else:
        lines = []
        for p in promos:
            st = "✅" if p.active else "⛔"
            lines.append(
                f"{st} <code>{p.code}</code> · {p.type}={p.value} · {p.used_count}/{p.usage_limit or '∞'}"
            )
        body = "\n".join(lines)
    await edit_or_send(
        call,
        f"🏷 <b>Промокоды</b>\n\n{body}\n\n"
        "Создать: /addpromo &lt;код&gt; &lt;percent|fixed|bonus_days&gt; &lt;значение&gt; [лимит] [new]",
        inline.admin_promo_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "adm:promo_new")
async def cb_promo_new(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await edit_or_send(
        call,
        "➕ Создание промокода через команду:\n\n"
        "<code>/addpromo КОД percent 20 100 new</code>\n\n"
        "типы: percent (%), fixed (₽), bonus_days (дни)\n"
        "лимит — опционально (0 = без лимита), new — только новые.",
        inline.admin_promo_menu(),
    )
    await call.answer()


@router.message(Command("addpromo"))
async def cmd_addpromo(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(
            "Формат: /addpromo &lt;код&gt; &lt;percent|fixed|bonus_days&gt; &lt;значение&gt; [лимит] [new]"
        )
        return
    code, type_, value = parts[1], parts[2], parts[3]
    if type_ not in ("percent", "fixed", "bonus_days"):
        await message.answer("Тип: percent | fixed | bonus_days")
        return
    try:
        value_f = float(value)
    except ValueError:
        await message.answer("Значение должно быть числом")
        return
    limit = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    only_new = "new" in parts[4:]
    await repo.create_promocode(code, type_, value_f, limit, only_new)
    await message.answer(f"✅ Промокод <code>{code.upper()}</code> создан.")


# ---------------- Рассылка ----------------

@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.broadcast_text)
    await edit_or_send(
        call,
        "📢 Отправь текст рассылки одним сообщением:",
        inline.back_to_menu(),
    )
    await call.answer()


@router.message(AdminStates.broadcast_text)
async def on_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    users = await repo.list_users(limit=10000)
    sent = 0
    for u in users:
        try:
            await bot.send_message(u.tg_id, message.html_text)
            sent += 1
        except Exception:
            continue
    await message.answer(f"✅ Рассылка отправлена: {sent} получателей.")


# ---------------- Лог антифрода ----------------

@router.callback_query(F.data == "adm:fraud")
async def cb_fraud(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    subs = await repo.all_active_subscriptions()
    flagged = [s for s in subs if s.violations > 0]
    if not flagged:
        body = "Нарушений нет."
    else:
        body = "\n".join(
            f"⚠️ sub#{s.id} · {plan_title(s.plan)} · нарушений: {s.violations}" for s in flagged
        )
    await edit_or_send(
        call,
        f"🔍 <b>Лог антифрода</b>\n\n{body}",
        inline.admin_menu(),
    )
    await call.answer()
