"""Ввод промокода (единственный пользовательский текстовый ввод)."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import get_tariff
from db import repo
from handlers.states import PromoStates
from keyboards import inline
from services import promo as promo_service
from utils.tg import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="promo")


@router.callback_query(F.data == "promo")
async def cb_promo(call: CallbackQuery, state: FSMContext) -> None:
    """Промокод из главного меню (без привязки к тарифу — информационно)."""
    await state.set_state(PromoStates.waiting_code)
    await state.update_data(plan=None)
    await edit_or_send(call, "🏷 Введи промокод одним сообщением:", inline.cancel_kb())
    await call.answer()


@router.callback_query(F.data.startswith("promo_for:"))
async def cb_promo_for(call: CallbackQuery, state: FSMContext) -> None:
    """Промокод при покупке конкретного тарифа."""
    plan = call.data.split(":", 1)[1]
    await state.set_state(PromoStates.waiting_code)
    await state.update_data(plan=plan)
    await edit_or_send(call, "🏷 Введи промокод одним сообщением:", inline.cancel_kb())
    await call.answer()


@router.message(PromoStates.waiting_code)
async def on_promo_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    data = await state.get_data()
    plan = data.get("plan")
    user = await repo.get_or_create_user(message.from_user.id, message.from_user.username)

    base_price = get_tariff(plan)["price"] if plan else get_tariff("solo")["price"]
    result = await promo_service.validate_and_apply(code, user.id, base_price, plan=plan)

    if not result.ok:
        await message.answer(f"❌ {result.message}", reply_markup=inline.cancel_kb())
        return

    if not plan:
        # информационно: промокод валиден, предлагаем выбрать тариф
        await state.clear()
        await message.answer(
            f"✅ Промокод <b>{code}</b> действителен!\n{result.message}\n\n"
            "Выбери тариф — промокод применим при оплате.",
            reply_markup=inline.tariffs_menu(),
        )
        return

    # применяем к выбранному тарифу
    new_price = result.new_price if result.new_price is not None else base_price
    await state.update_data(price=new_price, bonus_days=result.bonus_days, promocode=code)
    await state.set_state(None)
    tariff = get_tariff(plan)
    bonus = f"\n🎁 +{result.bonus_days} дн." if result.bonus_days else ""
    await message.answer(
        f"✅ Промокод <b>{code}</b> применён!\n{result.message}{bonus}\n\n"
        f"{tariff['emoji']} <b>{tariff['title']}</b> — к оплате <b>{new_price} ₽</b>",
        reply_markup=inline.buy_confirm(plan, new_price, promo_applied=True),
    )
