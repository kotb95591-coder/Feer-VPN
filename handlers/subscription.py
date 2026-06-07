"""«Моя подписка», ключ + QR, список устройств, экран бана и разбан."""
import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from config import config
from db import repo
from handlers import texts
from keyboards import inline
from utils.helpers import days_left, fmt_date, fmt_datetime, plan_title
from utils.helpers import gen_payment_code
from utils.qr import make_qr
from services.donationalerts import build_payment_instruction
from services import subscription as sub_service
from services.marzban import MarzbanError, marzban
from utils.tg import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="subscription")

# Сервер и БД живут в UTC; пользователи — в Москве (UTC+3, без перехода на лето).
_MSK_OFFSET = timedelta(hours=3)


def _msk(dt: datetime) -> datetime:
    """UTC-наивный datetime -> московское время для показа."""
    return dt + _MSK_OFFSET


@router.callback_query(F.data == "my_sub")
async def cb_my_sub(call: CallbackQuery) -> None:
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    sub = await repo.get_active_subscription(user.id)
    if not sub:
        await edit_or_send(call, "У тебя пока нет активной подписки.", inline.back_to_menu())
        await call.answer()
        return

    key = sub.vless_key or "—"
    text = (
        "🔑 <b>Моя подписка</b>\n\n"
        f"Тариф: <b>{plan_title(sub.plan)}</b>\n"
        f"Статус: <b>{'активна' if sub.status == 'active' else sub.status}</b>\n"
        f"Действует до: <b>{fmt_date(sub.expires_at)}</b> ({days_left(sub.expires_at)} дн.)\n"
        f"Устройств: до <b>{sub.device_limit}</b>\n\n"
        f"Ключ:\n<code>{key}</code>"
    )
    # отправляем QR отдельным сообщением с клавиатурой
    await call.message.delete()
    if sub.vless_key:
        qr = BufferedInputFile(make_qr(sub.vless_key).read(), filename="key.png")
        await call.message.answer_photo(photo=qr, caption=text, reply_markup=inline.my_sub_menu())
    else:
        await call.message.answer(text, reply_markup=inline.my_sub_menu())
    await call.answer()


@router.callback_query(F.data == "devices")
async def cb_devices(call: CallbackQuery) -> None:
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    sub = await repo.get_active_subscription(user.id)
    if not sub:
        await call.answer("Нет активной подписки", show_alert=True)
        return

    # На serverless интервальных задач нет — тянем живые данные из Marzban прямо сейчас.
    online_line = ""
    if sub.marzban_username and marzban.enabled:
        try:
            muser = await marzban.get_user(sub.marzban_username)
            online_at = muser.get("online_at") if isinstance(muser, dict) else None
            if online_at:
                try:
                    dt = datetime.fromisoformat(str(online_at).replace("Z", ""))
                    online_line = f"🟢 Последняя активность: <b>{fmt_datetime(_msk(dt))}</b>\n\n"
                except ValueError:
                    online_line = f"🟢 Последняя активность: <b>{online_at}</b>\n\n"
            else:
                online_line = "⚪️ Активных подключений сейчас не видно.\n\n"
        except MarzbanError:
            pass
        # Если на сервере включён модуль IP-Limit — подтянем актуальные IP-устройства.
        try:
            ips = await marzban.get_active_ips(sub.marzban_username)
            for ip in ips:
                await repo.upsert_device(sub.id, None, ip)
        except MarzbanError:
            pass

    devices = await repo.list_devices(sub.id)
    if not devices:
        body = "Пока нет активных подключений. Подключись по ключу — устройство появится здесь."
    else:
        lines = []
        for i, d in enumerate(devices, 1):
            mark = "🚫" if d.status == "banned" else "✅"
            ident = (d.hwid or d.last_ip or d.first_ip or "—")
            seen = fmt_datetime(_msk(d.last_seen)) if d.last_seen else "—"
            lines.append(f"{mark} {i}. <code>{ident}</code> · {seen}")
        body = "\n".join(lines)
    text = (
        f"📱 <b>Устройства</b> ({len(devices)}/{sub.device_limit})\n\n"
        f"{online_line}{body}\n\n"
        "⚠️ Превышение лимита ведёт к бану лишнего устройства."
    )
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=inline.my_sub_menu())
    else:
        await call.message.edit_text(text, reply_markup=inline.my_sub_menu())
    await call.answer()


@router.callback_query(F.data == "unban")
async def cb_unban(call: CallbackQuery, state: FSMContext) -> None:
    """Разбан с баланса (списывается PRICE_UNBAN, включает Solo)."""
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    balance = await repo.get_balance(user.id)
    price = float(config.PRICE_UNBAN)

    if balance < price:
        need = price - balance
        await edit_or_send(
            call,
            "🔓 <b>Разбан аккаунта</b>\n\n"
            f"Стоимость: <b>{int(price)} ₽</b> (включает Solo на месяц).\n"
            f"💼 На балансе: <b>{balance:.0f} ₽</b> — не хватает <b>{need:.0f} ₽</b>.\n\n"
            "Пополни баланс и вернись к разбану.",
            inline.need_topup_menu(),
        )
        await call.answer()
        return

    try:
        sub, _ = await sub_service.unban_with_balance(user)
    except sub_service.InsufficientFundsError:
        await edit_or_send(
            call,
            "💼 На балансе недостаточно средств. Пополни кабинет и попробуй снова.",
            inline.need_topup_menu(),
        )
        await call.answer()
        return
    except sub_service.SubscriptionError as e:
        log.error("Разбан с баланса: %s", e)
        await edit_or_send(
            call,
            "⚠️ Не удалось завершить разбан. Напиши в поддержку.",
            inline.back_to_menu(),
        )
        await call.answer()
        return

    key = sub.vless_key or ""
    new_balance = await repo.get_balance(user.id)
    caption = (
        "✅ <b>Аккаунт разбанен!</b>\n\n"
        f"Подписка Solo активна до <b>{fmt_date(sub.expires_at)}</b>.\n"
        f"💼 Остаток на балансе: <b>{new_balance:.0f} ₽</b>\n\n"
        f"🔑 <code>{key}</code>"
    )
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass
    if key:
        qr = BufferedInputFile(make_qr(key).read(), filename="key.png")
        await call.message.answer_photo(photo=qr, caption=caption, reply_markup=inline.my_sub_menu())
    else:
        await call.message.answer(caption, reply_markup=inline.my_sub_menu())
    await call.answer()
