"""Покупка подписки: выбор тарифа → (промокод) → создание платежа."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import config, get_tariff
from db import repo
from handlers import texts
from keyboards import inline
from services.donationalerts import build_payment_instruction
from utils.tg import edit_or_send, edit_or_send_media
from utils.helpers import gen_payment_code

log = logging.getLogger(__name__)
router = Router(name="buy")


@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send_media(call, texts.tariffs_text(), inline.tariffs_menu(), config.IMG_TARIFFS)
    await call.answer()


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(call: CallbackQuery, state: FSMContext) -> None:
    plan = call.data.split(":", 1)[1]
    tariff = get_tariff(plan)
    if not tariff:
        await call.answer("Неизвестный тариф", show_alert=True)
        return
    await state.update_data(plan=plan, price=tariff["price"], bonus_days=0, promocode=None)
    # Сначала показываем правила — продолжить можно только после согласия.
    await edit_or_send(call, texts.rules_text(tariff), inline.rules_confirm(plan))
    await call.answer()


@router.callback_query(F.data.startswith("agree:"))
async def cb_agree(call: CallbackQuery, state: FSMContext) -> None:
    plan = call.data.split(":", 1)[1]
    tariff = get_tariff(plan)
    if not tariff:
        await call.answer("Неизвестный тариф", show_alert=True)
        return
    await state.update_data(plan=plan, price=tariff["price"], bonus_days=0, promocode=None)
    text = (
        f"{tariff['emoji']} <b>{tariff['title']}</b>\n\n"
        f"Цена: <b>{tariff['price']} ₽</b> / {tariff['days']} дней\n"
        f"Устройства: <b>{tariff['desc']}</b>\n\n"
        "✅ С правилами ознакомлен(а).\n"
        "Можно ввести промокод или сразу оплатить."
    )
    await edit_or_send(call, text, inline.buy_confirm(plan, tariff["price"]))
    await call.answer()


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(call: CallbackQuery, state: FSMContext) -> None:
    plan = call.data.split(":", 1)[1]
    data = await state.get_data()
    tariff = get_tariff(plan)
    if not tariff:
        await call.answer("Неизвестный тариф", show_alert=True)
        return

    price = data.get("price", tariff["price"])
    promocode = data.get("promocode")

    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    code = gen_payment_code()
    payment = await repo.create_payment(
        user_id=user.id,
        amount=float(price),
        code=code,
        type_="subscription",
        plan=plan,
        promocode=promocode,
    )
    await state.update_data(payment_id=payment.id)

    await edit_or_send(
        call,
        build_payment_instruction(code, price),
        inline.payment_check(payment.id),
    )
    await call.answer()
