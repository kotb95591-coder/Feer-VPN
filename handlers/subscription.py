"""«Моя подписка», ключ + QR, список устройств, экран бана и разбан."""
import logging

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
from utils.tg import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="subscription")


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
    devices = await repo.list_devices(sub.id)
    if not devices:
        body = "Пока нет подключённых устройств."
    else:
        lines = []
        for i, d in enumerate(devices, 1):
            mark = "🚫" if d.status == "banned" else "✅"
            hwid = (d.hwid or d.first_ip or "—")[:18]
            lines.append(f"{mark} {i}. <code>{hwid}</code> · {fmt_datetime(d.last_seen)}")
        body = "\n".join(lines)
    text = (
        f"📱 <b>Устройства</b> ({len(devices)}/{sub.device_limit})\n\n{body}\n\n"
        "⚠️ Превышение лимита ведёт к бану лишнего устройства."
    )
    await call.message.edit_caption(caption=text, reply_markup=inline.my_sub_menu()) if call.message.photo else await call.message.edit_text(text, reply_markup=inline.my_sub_menu())
    await call.answer()


@router.callback_query(F.data == "unban")
async def cb_unban(call: CallbackQuery, state: FSMContext) -> None:
    """Оплата разбана (300 ₽, включает Solo)."""
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    code = gen_payment_code("UNBAN")
    payment = await repo.create_payment(
        user_id=user.id, amount=float(config.PRICE_UNBAN), code=code, type_="unban"
    )
    await edit_or_send(
        call,
        "🔓 <b>Разбан аккаунта</b>\n\n"
        + build_payment_instruction(code, config.PRICE_UNBAN)
        + "\n\nПосле оплаты аккаунт разбанят и выдадут подписку Solo.",
        inline.payment_check(payment.id),
    )
    await call.answer()
