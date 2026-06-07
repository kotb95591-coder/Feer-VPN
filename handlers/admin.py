"""Админ-панель (только для tg_id из ADMIN_IDS). Всё на кнопках/визардах, без команд."""
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import config
from db import repo
from handlers.states import AdminStates
from keyboards import inline
from services import subscription as sub_service
from services.marzban import MarzbanError, marzban
from utils.helpers import fmt_date, plan_title
from utils.tg import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="admin")

_PLAN_LABEL = {"all": "Все тарифы", "solo": "Solo", "family": "Семья"}
_SUB_STATUS_LABEL = {
    "active": "активна",
    "disabled": "отключена",
    "expired": "приостановлена",
    "banned": "забанена",
    "pending": "ожидает оплаты",
}


def _is_admin(tg_id: int) -> bool:
    return config.is_admin(tg_id)


def _parse_nonneg(text: str) -> int | None:
    """Парсит целое число >= 0, иначе None."""
    raw = (text or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


async def _send(target, text: str, markup) -> None:
    """Универсальная отправка для CallbackQuery и Message."""
    if isinstance(target, CallbackQuery):
        await edit_or_send(target, text, markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


async def _client_card_text(user) -> str:
    active = await repo.get_active_subscription(user.id)
    latest = await repo.get_latest_subscription(user.id)
    balance = await repo.get_balance(user.id)
    uname = f"@{user.username}" if user.username else "—"
    show = active or latest
    if not show:
        sub_info = "📦 Подписки нет"
    elif active:
        sub_info = (
            f"📦 Подписка: {plan_title(active.plan)} — активна "
            f"до {fmt_date(active.expires_at)}"
        )
    else:
        st = _SUB_STATUS_LABEL.get(latest.status, latest.status)
        sub_info = (
            f"📦 Подписка: {plan_title(latest.plan)} — {st} "
            f"(до {fmt_date(latest.expires_at)})"
        )
    return (
        f"👤 <b>Клиент</b> {uname}\n"
        f"tg: <code>{user.tg_id}</code>\n"
        f"Статус: {user.status} · нарушений: {user.violations}\n"
        f"💼 Баланс: {balance:.0f} ₽\n"
        f"{sub_info}"
    )


async def _client_markup(user):
    """Клавиатура карточки клиента с учётом состояния подписки."""
    active = await repo.get_active_subscription(user.id)
    latest = await repo.get_latest_subscription(user.id)
    can_disable = active is not None
    can_enable = (
        active is None
        and latest is not None
        and latest.status == "disabled"
        and bool(latest.marzban_username)
    )
    return inline.admin_client_actions(
        user.id, can_disable=can_disable, can_enable=can_enable
    )


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


# ---------------- Главное меню ----------------

@router.callback_query(F.data == "admin")
async def cb_admin(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
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
    balances = await repo.total_balance()
    solo = sum(1 for s in active if s.plan == "solo")
    family = sum(1 for s in active if s.plan == "family")
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: <b>{users}</b>\n"
        f"Активных подписок: <b>{len(active)}</b>\n"
        f"· Solo: {solo}\n· Семья: {family}\n\n"
        f"💰 Выручка (всего): <b>{int(revenue)} ₽</b>\n"
        f"💼 На балансах клиентов: <b>{balances:.0f} ₽</b>"
    )
    await edit_or_send(call, text, inline.admin_menu())
    await call.answer()


# ---------------- Клиенты ----------------

@router.callback_query(F.data == "adm:clients")
async def cb_clients(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    users = await repo.list_users(limit=20)
    if not users:
        await edit_or_send(call, "👥 <b>Клиенты</b>\n\nПока нет клиентов.", inline.admin_clients_list([]))
    else:
        await edit_or_send(
            call,
            "👥 <b>Клиенты</b> (последние 20)\nВыбери клиента или найди по ID/@username:",
            inline.admin_clients_list(users),
        )
    await call.answer()


@router.callback_query(F.data.startswith("adm:client:"))
async def cb_client(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    user_id = int(call.data.split(":")[2])
    user = await repo.get_user_by_id(user_id)
    if not user:
        await call.answer("Не найден", show_alert=True)
        return
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))
    await call.answer()


@router.callback_query(F.data == "adm:find_client")
async def cb_find_client(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.find_client)
    await edit_or_send(call, "🔎 Введи tg_id или @username клиента:", inline.admin_cancel())
    await call.answer()


@router.message(AdminStates.find_client)
async def on_find_client(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    q = (message.text or "").strip()
    if q.lstrip("-").isdigit():
        user = await repo.get_user(int(q))
    else:
        user = await repo.get_user_by_username(q)
    if not user:
        await message.answer(
            "Клиент не найден. Попробуй ещё раз или вернись назад.",
            reply_markup=inline.admin_cancel(),
        )
        return
    await state.clear()
    await message.answer(await _client_card_text(user), reply_markup=await _client_markup(user))


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
    user = await repo.get_user_by_id(user_id)
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


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
    await call.answer("Разбанен", show_alert=True)
    user = await repo.get_user_by_id(user_id)
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


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
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


# ---------------- Отключение / включение подписки ----------------

@router.callback_query(F.data.startswith("adm:sub_off:"))
async def cb_sub_off(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    user = await repo.get_user_by_id(user_id)
    sub = await repo.get_active_subscription(user_id) if user else None
    if not sub:
        await call.answer("Нет активной подписки", show_alert=True)
        return
    await repo.set_subscription_status(sub.id, "disabled")
    if sub.marzban_username:
        try:
            await marzban.ban(sub.marzban_username)
        except MarzbanError as e:
            log.error("Не удалось отключить %s в Marzban: %s", sub.marzban_username, e)
            await call.answer(f"В БД отключено, но Marzban вернул ошибку: {e}", show_alert=True)
            user = await repo.get_user_by_id(user_id)
            await edit_or_send(call, await _client_card_text(user), await _client_markup(user))
            return
    try:
        await call.bot.send_message(
            user.tg_id, "⛔ Ваша подписка отключена администратором. Доступ приостановлен."
        )
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Подписка отключена (и в Marzban)", show_alert=True)
    user = await repo.get_user_by_id(user_id)
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


@router.callback_query(F.data.startswith("adm:sub_on:"))
async def cb_sub_on(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    user = await repo.get_user_by_id(user_id)
    sub = await repo.get_latest_subscription(user_id) if user else None
    if not sub or sub.status != "disabled":
        await call.answer("Нет отключённой подписки", show_alert=True)
        return
    await repo.set_subscription_status(sub.id, "active")
    if sub.marzban_username:
        try:
            await marzban.unban(sub.marzban_username)
        except MarzbanError as e:
            log.error("Не удалось включить %s в Marzban: %s", sub.marzban_username, e)
            await call.answer(f"В БД включено, но Marzban вернул ошибку: {e}", show_alert=True)
            user = await repo.get_user_by_id(user_id)
            await edit_or_send(call, await _client_card_text(user), await _client_markup(user))
            return
    try:
        await call.bot.send_message(user.tg_id, "✅ Ваша подписка снова активна.")
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Подписка включена (и в Marzban)", show_alert=True)
    user = await repo.get_user_by_id(user_id)
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


# ---------------- Быстрая выдача с карточки клиента ----------------

@router.callback_query(F.data.startswith("adm:give:"))
async def cb_give_quick(call: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")
    user_id, plan = int(parts[2]), parts[3]
    user = await repo.get_user_by_id(user_id)
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
    await call.answer(f"{plan_title(plan)} {action} до {fmt_date(sub.expires_at)}", show_alert=True)
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


# ---------------- Визард выдачи подписки ----------------

async def _do_grant(target, state: FSMContext, bot: Bot, days: int | None) -> None:
    data = await state.get_data()
    plan = data.get("give_plan")
    user_id = data.get("give_user_id")
    user = await repo.get_user_by_id(user_id) if user_id else None
    if not plan or not user:
        await state.clear()
        await _send(target, "⚠️ Сессия выдачи истекла. Начни заново.", inline.admin_menu())
        return
    try:
        sub, is_new = await sub_service.admin_grant(user, plan, days)
    except sub_service.SubscriptionError as e:
        await state.clear()
        await _send(target, f"⚠️ Не удалось выдать: {e}", inline.admin_menu())
        return
    await _notify_granted(bot, user, plan, sub)
    await state.clear()
    action = "выдана" if is_new else "продлена"
    uname = f"@{user.username}" if user.username else f"id{user.tg_id}"
    await _send(
        target,
        f"✅ Подписка {plan_title(plan)} {action} клиенту {uname} до <b>{fmt_date(sub.expires_at)}</b>.",
        inline.admin_menu(),
    )


@router.callback_query(F.data == "adm:give")
async def cb_give_start(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await edit_or_send(call, "🎁 <b>Выдать подписку</b>\n\nКому выдаём?", inline.admin_give_recipient())
    await call.answer()


@router.callback_query(F.data == "adm:give_self")
async def cb_give_self(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    await state.update_data(give_user_id=user.id)
    await state.set_state(None)
    await edit_or_send(call, "🎁 Выбери тариф для выдачи:", inline.admin_give_plan())
    await call.answer()


@router.callback_query(F.data == "adm:give_other")
async def cb_give_other(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.give_target)
    await edit_or_send(call, "🔎 Введи tg_id или @username получателя:", inline.admin_cancel())
    await call.answer()


@router.message(AdminStates.give_target)
async def on_give_target(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    q = (message.text or "").strip()
    if q.lstrip("-").isdigit():
        user = await repo.get_user(int(q))
    else:
        user = await repo.get_user_by_username(q)
    if not user:
        await message.answer(
            "Пользователь не найден — он должен хотя бы раз запустить бота (/start). Попробуй ещё раз.",
            reply_markup=inline.admin_cancel(),
        )
        return
    await state.update_data(give_user_id=user.id)
    await state.set_state(None)
    uname = f"@{user.username}" if user.username else f"id{user.tg_id}"
    await message.answer(f"Получатель: {uname}\n\n🎁 Выбери тариф:", reply_markup=inline.admin_give_plan())


@router.callback_query(F.data.startswith("adm:give_plan:"))
async def cb_give_plan(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    plan = call.data.split(":")[2]
    await state.update_data(give_plan=plan)
    await edit_or_send(
        call,
        f"Тариф: <b>{plan_title(plan)}</b>\n\n⏳ На сколько дней выдать?",
        inline.admin_give_days(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:give_days:"))
async def cb_give_days(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    arg = call.data.split(":")[2]
    days = None if arg == "tariff" else int(arg)
    await _do_grant(call, state, bot, days)


@router.callback_query(F.data == "adm:give_days_custom")
async def cb_give_days_custom(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.give_days)
    await edit_or_send(call, "⏳ Введи количество дней (число):", inline.admin_cancel())
    await call.answer()


@router.message(AdminStates.give_days)
async def on_give_days(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    days = _parse_nonneg(message.text)
    if not days:
        await message.answer("Введи положительное число дней.", reply_markup=inline.admin_cancel())
        return
    await _do_grant(message, state, bot, days)


# ---------------- Баланс ----------------

@router.callback_query(F.data.startswith("adm:addbal_custom:"))
async def cb_addbal_custom(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    user_id = int(call.data.split(":")[2])
    await state.set_state(AdminStates.addbal_amount)
    await state.update_data(addbal_user_id=user_id)
    await edit_or_send(call, "💰 Введи сумму начисления (₽):", inline.admin_cancel())
    await call.answer()


@router.message(AdminStates.addbal_amount)
async def on_addbal_amount(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    amount = _parse_nonneg(message.text)
    if not amount:
        await message.answer("Введи положительную сумму (целое число).", reply_markup=inline.admin_cancel())
        return
    data = await state.get_data()
    user_id = data.get("addbal_user_id")
    user = await repo.get_user_by_id(user_id) if user_id else None
    if not user:
        await state.clear()
        await message.answer("Клиент не найден.", reply_markup=inline.admin_menu())
        return
    new_balance = await repo.add_balance(user.id, float(amount), "admin", "Начисление админом")
    try:
        await message.bot.send_message(
            user.tg_id,
            f"💼 Тебе начислено <b>{amount} ₽</b> на баланс.\n"
            f"💰 Текущий баланс: <b>{new_balance:.0f} ₽</b>.",
        )
    except Exception:  # noqa: BLE001
        pass
    await state.clear()
    await message.answer(await _client_card_text(user), reply_markup=await _client_markup(user))


@router.callback_query(F.data.startswith("adm:addbal:"))
async def cb_addbal(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    _, _, user_id_s, amount_s = call.data.split(":")
    user = await repo.get_user_by_id(int(user_id_s))
    if not user:
        await call.answer("Не найден", show_alert=True)
        return
    amount = float(amount_s)
    new_balance = await repo.add_balance(user.id, amount, "admin", "Начисление админом")
    try:
        await call.bot.send_message(
            user.tg_id,
            f"💼 Тебе начислено <b>{int(amount)} ₽</b> на баланс.\n"
            f"💰 Текущий баланс: <b>{new_balance:.0f} ₽</b>.",
        )
    except Exception:  # noqa: BLE001
        pass
    await call.answer(f"Начислено {int(amount)} ₽. Баланс: {new_balance:.0f} ₽", show_alert=True)
    user = await repo.get_user_by_id(int(user_id_s))
    await edit_or_send(call, await _client_card_text(user), await _client_markup(user))


# ---------------- Ответ в поддержку ----------------

@router.callback_query(F.data.startswith("adm:reply:"))
async def cb_reply(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    tg_id = int(call.data.split(":")[2])
    await state.set_state(AdminStates.reply_text)
    await state.update_data(reply_to=tg_id)
    await edit_or_send(call, f"✏️ Введи ответ для пользователя tg:<code>{tg_id}</code>:", inline.admin_cancel())
    await call.answer()


@router.message(AdminStates.reply_text)
async def on_reply_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    tg_id = data.get("reply_to")
    await state.clear()
    if not tg_id:
        await message.answer("⚠️ Сессия истекла.", reply_markup=inline.admin_menu())
        return
    try:
        await message.bot.send_message(tg_id, f"💬 <b>Ответ поддержки</b>\n\n{message.html_text}")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"⚠️ Не удалось отправить: {e}", reply_markup=inline.admin_menu())
        return
    await message.answer("✅ Ответ отправлен пользователю.", reply_markup=inline.admin_menu())


# ---------------- Промокоды ----------------

def _promo_line(p) -> str:
    st = "✅" if p.active else "⛔"
    percent = float(getattr(p, "percent", 0) or 0)
    fixed = float(getattr(p, "fixed_price", 0) or 0)
    bonus = int(getattr(p, "bonus_days", 0) or 0)
    effects = []
    if percent == 0 and fixed == 0 and bonus == 0:
        effects.append(f"{p.type}={p.value:g}")
    else:
        if fixed > 0:
            effects.append(f"цена {int(fixed)}₽")
        if percent > 0:
            effects.append(f"−{int(percent)}%")
        if bonus > 0:
            effects.append(f"+{bonus}дн")
    target = _PLAN_LABEL.get(getattr(p, "target_plan", "all") or "all", "Все тарифы")
    limit = p.usage_limit or "∞"
    return f"{st} <code>{p.code}</code> · {', '.join(effects)} · {target} · {p.used_count}/{limit}"


@router.callback_query(F.data == "adm:promos")
async def cb_promos(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    promos = await repo.list_promocodes()
    body = "\n".join(_promo_line(p) for p in promos) if promos else "Промокодов пока нет."
    await edit_or_send(
        call,
        f"🏷 <b>Промокоды</b>\n\n{body}\n\nВыбери промокод для управления или создай новый:",
        inline.admin_promo_list(promos),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:promo_del_yes:"))
async def cb_promo_del_yes(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[2])
    await repo.delete_promocode(promo_id)
    await state.clear()
    promos = await repo.list_promocodes()
    body = "\n".join(_promo_line(p) for p in promos) if promos else "Промокодов пока нет."
    await call.answer("Промокод удалён", show_alert=True)
    await edit_or_send(call, f"🏷 <b>Промокоды</b>\n\n{body}", inline.admin_promo_list(promos))


@router.callback_query(F.data.startswith("adm:promo_del:"))
async def cb_promo_del(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[2])
    promo = await repo.get_promocode_by_id(promo_id)
    if not promo:
        await call.answer("Не найден", show_alert=True)
        return
    await edit_or_send(
        call,
        f"🗑 Удалить промокод <code>{promo.code}</code>?\n\nЭто действие необратимо.",
        inline.admin_promo_del_confirm(promo_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:promo_toggle:"))
async def cb_promo_toggle(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[2])
    promo = await repo.get_promocode_by_id(promo_id)
    if not promo:
        await call.answer("Не найден", show_alert=True)
        return
    updated = await repo.update_promocode(promo_id, active=not promo.active)
    await call.answer("Включён" if updated.active else "Выключен", show_alert=True)
    await edit_or_send(
        call,
        f"🏷 <b>Промокод</b>\n\n{_promo_line(updated)}",
        inline.admin_promo_card(updated),
    )


@router.callback_query(F.data.startswith("adm:promo_edit:"))
async def cb_promo_edit(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[2])
    promo = await repo.get_promocode_by_id(promo_id)
    if not promo:
        await call.answer("Не найден", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        edit_id=promo.id,
        code=promo.code,
        percent=int(promo.percent or 0),
        fixed=int(promo.fixed_price or 0),
        bonus=int(promo.bonus_days or 0),
        limit=int(promo.usage_limit or 0),
    )
    await state.set_state(AdminStates.promo_percent)
    await edit_or_send(
        call,
        f"✏️ <b>Редактирование</b> <code>{promo.code}</code>\n\n"
        f"Текущая скидка: {int(promo.percent or 0)}%\n"
        f"Шаг 1/5 — введи новый процент скидки в % (0 — без скидки):",
        inline.admin_cancel(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:promo:"))
async def cb_promo_card(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    promo_id = int(call.data.split(":")[2])
    promo = await repo.get_promocode_by_id(promo_id)
    if not promo:
        await call.answer("Не найден", show_alert=True)
        return
    await edit_or_send(
        call,
        f"🏷 <b>Промокод</b>\n\n{_promo_line(promo)}",
        inline.admin_promo_card(promo),
    )
    await call.answer()


@router.callback_query(F.data == "adm:promo_new")
async def cb_promo_new(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminStates.promo_code)
    await edit_or_send(
        call,
        "➕ <b>Новый промокод</b>\n\nШаг 1/6 — введи код (например SUMMER):",
        inline.admin_cancel(),
    )
    await call.answer()


@router.message(AdminStates.promo_code)
async def on_promo_code(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    code = (message.text or "").strip().upper()
    if not code or " " in code:
        await message.answer("Код без пробелов, одним словом. Попробуй ещё раз:", reply_markup=inline.admin_cancel())
        return
    if await repo.get_promocode(code):
        await message.answer("Такой промокод уже существует. Введи другой код:", reply_markup=inline.admin_cancel())
        return
    await state.update_data(code=code)
    await state.set_state(AdminStates.promo_percent)
    await message.answer(
        f"Код: <b>{code}</b>\n\nШаг 2/6 — процент скидки в % (0 — без скидки):",
        reply_markup=inline.admin_cancel(),
    )


@router.message(AdminStates.promo_percent)
async def on_promo_percent(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    val = _parse_nonneg(message.text)
    if val is None or val > 100:
        await message.answer("Введи число от 0 до 100:", reply_markup=inline.admin_cancel())
        return
    await state.update_data(percent=val)
    await state.set_state(AdminStates.promo_fixed)
    await message.answer(
        "Шаг 3/6 — спец-цена в ₽ для подписки (0 — без спец-цены):",
        reply_markup=inline.admin_cancel(),
    )


@router.message(AdminStates.promo_fixed)
async def on_promo_fixed(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    val = _parse_nonneg(message.text)
    if val is None:
        await message.answer("Введи целое число ≥ 0:", reply_markup=inline.admin_cancel())
        return
    await state.update_data(fixed=val)
    await state.set_state(AdminStates.promo_bonus)
    await message.answer(
        "Шаг 4/6 — бонусные дни к подписке (0 — без бонуса):",
        reply_markup=inline.admin_cancel(),
    )


@router.message(AdminStates.promo_bonus)
async def on_promo_bonus(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    val = _parse_nonneg(message.text)
    if val is None:
        await message.answer("Введи целое число ≥ 0:", reply_markup=inline.admin_cancel())
        return
    data = await state.get_data()
    percent = int(data.get("percent", 0))
    fixed = int(data.get("fixed", 0))
    if percent == 0 and fixed == 0 and val == 0:
        await state.set_state(AdminStates.promo_percent)
        await message.answer(
            "Промокод без эффектов. Укажи хотя бы один параметр.\n\nШаг 2/6 — процент скидки в % (0 — без скидки):",
            reply_markup=inline.admin_cancel(),
        )
        return
    await state.update_data(bonus=val)
    await state.set_state(AdminStates.promo_limit)
    await message.answer(
        "Шаг 5/6 — сколько активаций у промокода (0 — без ограничения):",
        reply_markup=inline.admin_cancel(),
    )


@router.message(AdminStates.promo_limit)
async def on_promo_limit(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    val = _parse_nonneg(message.text)
    if val is None:
        await message.answer("Введи целое число ≥ 0 (0 — без ограничения):", reply_markup=inline.admin_cancel())
        return
    await state.update_data(limit=val)
    await state.set_state(None)
    await message.answer(
        "Шаг 6/6 — для какого тарифа действует промокод?",
        reply_markup=inline.admin_promo_plan(),
    )


@router.callback_query(F.data.startswith("adm:promo_plan:"))
async def cb_promo_plan(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    target = call.data.split(":")[2]
    data = await state.get_data()
    code = data.get("code")
    if not code:
        await state.clear()
        await edit_or_send(call, "⚠️ Сессия истекла, начни заново.", inline.admin_promo_menu())
        await call.answer()
        return
    percent = int(data.get("percent", 0))
    fixed = int(data.get("fixed", 0))
    bonus = int(data.get("bonus", 0))
    limit = int(data.get("limit", 0))
    edit_id = data.get("edit_id")
    if edit_id:
        await repo.update_promocode(
            edit_id,
            percent=float(percent),
            fixed_price=float(fixed),
            bonus_days=bonus,
            usage_limit=limit,
            target_plan=target,
        )
        verb = "обновлён"
    else:
        await repo.create_promocode(
            code=code,
            usage_limit=limit,
            only_new=False,
            percent=float(percent),
            fixed_price=float(fixed),
            bonus_days=bonus,
            target_plan=target,
        )
        verb = "создан"
    await state.clear()
    effects = []
    if fixed > 0:
        effects.append(f"спец-цена {fixed} ₽")
    if percent > 0:
        effects.append(f"скидка {percent}%")
    if bonus > 0:
        effects.append(f"+{bonus} дней")
    eff = ", ".join(effects) if effects else "—"
    limit_txt = limit if limit > 0 else "∞"
    await edit_or_send(
        call,
        f"✅ Промокод <code>{code}</code> {verb}!\n\n"
        f"Эффект: {eff}\n"
        f"Активаций: {limit_txt}\n"
        f"Тариф: {_PLAN_LABEL.get(target, target)}",
        inline.admin_promo_menu(),
    )
    await call.answer()


# ---------------- Рассылка ----------------

@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.broadcast_text)
    await edit_or_send(call, "📢 Отправь текст рассылки одним сообщением:", inline.admin_cancel())
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
    await message.answer(f"✅ Рассылка отправлена: {sent} получателей.", reply_markup=inline.admin_menu())


# ---------------- Лог антифрода ----------------

@router.callback_query(F.data == "adm:fraud")
async def cb_fraud(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    subs = await repo.all_active_subscriptions()
    flagged = [s for s in subs if s.violations > 0]
    if not flagged:
        body = "Нарушений нет."
    else:
        body = "\n".join(
            f"⚠️ sub#{s.id} · {plan_title(s.plan)} · нарушений: {s.violations}" for s in flagged
        )
    await edit_or_send(call, f"🔍 <b>Лог антифрода</b>\n\n{body}", inline.admin_menu())
    await call.answer()
