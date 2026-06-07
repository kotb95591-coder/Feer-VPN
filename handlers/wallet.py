"""Личный кабинет: баланс, пополнение, история операций."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import config
from db import repo
from handlers.states import TopupStates
from keyboards import inline
from services.donationalerts import build_payment_instruction
from utils.helpers import fmt_date, fmt_datetime, gen_payment_code, plan_title
from utils.tg import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="wallet")


async def _cabinet_text(user) -> tuple[str, bool]:
    balance = await repo.get_balance(user.id)
    sub = await repo.get_active_subscription(user.id)
    has_sub = bool(sub)
    if sub:
        sub_line = (
            f"📦 Подписка: <b>{plan_title(sub.plan)}</b> — активна до "
            f"<b>{fmt_date(sub.expires_at)}</b>"
        )
    else:
        paused = await repo.get_resumable_subscription(user.id)
        if paused:
            sub_line = (
                f"⏸ Подписка <b>{plan_title(paused.plan)}</b> приостановлена — "
                "пополни баланс, и она возобновится автоматически."
            )
        else:
            sub_line = "📦 Активной подписки нет."
    text = (
        "💼 <b>Личный кабинет</b>\n\n"
        f"💰 Баланс: <b>{balance:.0f} ₽</b>\n"
        f"{sub_line}\n\n"
        "Деньги хранятся в кабинете — подписка покупается и продлевается "
        "автоматически с баланса каждый месяц."
    )
    return text, has_sub


@router.callback_query(F.data == "cabinet")
async def cb_cabinet(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    text, has_sub = await _cabinet_text(user)
    await edit_or_send(call, text, inline.cabinet_menu(has_sub))
    await call.answer()


@router.callback_query(F.data == "topup")
async def cb_topup(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(
        call,
        "💳 <b>Пополнение баланса</b>\n\nВыбери сумму или введи свою:",
        inline.topup_menu(),
    )
    await call.answer()


async def _create_topup(user, amount: float):
    code = gen_payment_code("TOPUP")
    return await repo.create_payment(
        user_id=user.id, amount=float(amount), code=code, type_="topup"
    )


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup_preset(call: CallbackQuery) -> None:
    amount = int(call.data.split(":", 1)[1])
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    payment = await _create_topup(user, amount)
    await edit_or_send(
        call,
        f"💳 <b>Пополнение на {amount} ₽</b>\n\n"
        + build_payment_instruction(payment.code, amount),
        inline.payment_check(payment.id),
    )
    await call.answer()


@router.callback_query(F.data == "topup_custom")
async def cb_topup_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopupStates.waiting_amount)
    await edit_or_send(
        call,
        f"✏️ Введи сумму пополнения в рублях (минимум {config.MIN_TOPUP} ₽):",
        inline.cancel_kb(),
    )
    await call.answer()


@router.message(TopupStates.waiting_amount)
async def on_topup_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "").replace("₽", "")
    if not raw.isdigit():
        await message.answer("Введи сумму числом, например 300.", reply_markup=inline.cancel_kb())
        return
    amount = int(raw)
    if amount < config.MIN_TOPUP:
        await message.answer(
            f"Минимальная сумма пополнения — {config.MIN_TOPUP} ₽.",
            reply_markup=inline.cancel_kb(),
        )
        return
    await state.clear()
    user = await repo.get_or_create_user(message.from_user.id, message.from_user.username)
    payment = await _create_topup(user, amount)
    await message.answer(
        f"💳 <b>Пополнение на {amount} ₽</b>\n\n"
        + build_payment_instruction(payment.code, amount),
        reply_markup=inline.payment_check(payment.id),
    )


@router.callback_query(F.data == "tx_history")
async def cb_tx_history(call: CallbackQuery) -> None:
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    txs = await repo.list_transactions(user.id, limit=10)
    balance = await repo.get_balance(user.id)
    if not txs:
        body = "Операций пока нет."
    else:
        lines = []
        for t in txs:
            sign = "➕" if t.amount > 0 else "➖"
            descr = t.description or t.kind
            lines.append(
                f"{sign} <b>{abs(t.amount):.0f} ₽</b> · {descr} · {fmt_datetime(t.created_at)}"
            )
        body = "\n".join(lines)
    has_sub = bool(await repo.get_active_subscription(user.id))
    await edit_or_send(
        call,
        f"🧾 <b>История операций</b>\n\n💰 Баланс: <b>{balance:.0f} ₽</b>\n\n{body}",
        inline.cabinet_menu(has_sub),
    )
    await call.answer()
