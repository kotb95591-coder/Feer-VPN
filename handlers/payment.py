"""Проверка оплаты (DonationAlerts) и выдача ключа/разбана."""
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from config import config, get_tariff
from db import repo
from keyboards import inline
from services import promo as promo_service
from services import subscription as sub_service
from services.donationalerts import DonationAlertsError, donation_alerts
from utils.helpers import fmt_date
from utils.qr import make_qr

log = logging.getLogger(__name__)
router = Router(name="payment")


async def _deliver_subscription(
    call: CallbackQuery, payment, bot: Bot
) -> None:
    """Выдаёт ключ после успешной оплаты подписки."""
    user = await repo.get_user_by_id(payment.user_id)
    tariff = get_tariff(payment.plan)
    bonus_days = 0

    # применяем промокод (бонусные дни) и фиксируем редемпцию
    if payment.promocode:
        result = await promo_service.validate_and_apply(
            payment.promocode, user.id, tariff["price"]
        )
        if result.ok and result.promo:
            bonus_days = result.bonus_days
            await promo_service.redeem(result.promo.id, user.id)

    try:
        sub, is_new = await sub_service.issue_or_extend(user, payment.plan, bonus_days)
    except sub_service.SubscriptionError as e:
        log.error("Ошибка выдачи подписки: %s", e)
        await call.message.answer(
            "⚠️ Оплата принята, но при выдаче ключа произошла ошибка. "
            "Напиши в поддержку — мы всё решим.",
            reply_markup=inline.back_to_menu(),
        )
        return

    key = sub.vless_key or ""
    caption = (
        "✅ <b>Оплата получена!</b>\n\n"
        f"Тариф: <b>{tariff['title']}</b>\n"
        f"Действует до: <b>{fmt_date(sub.expires_at)}</b>\n"
        f"Устройства: <b>{tariff['desc']}</b>\n\n"
        "🔑 <b>Твой ключ</b> (нажми, чтобы скопировать):\n"
        f"<code>{key}</code>\n\n"
        "Отсканируй QR или скопируй ключ в приложение."
    )
    qr = BufferedInputFile(make_qr(key).read(), filename="key.png")
    await call.message.answer_photo(
        photo=qr, caption=caption, reply_markup=inline.my_sub_menu()
    )


@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    payment_id = int(call.data.split(":", 1)[1])
    payment = await repo.get_payment(payment_id)
    if not payment:
        await call.answer("Платёж не найден", show_alert=True)
        return
    if payment.status == "paid":
        # платёж уже зачтён — повторно выдаём ключ (идемпотентно:
        # на случай если в прошлый раз выдача не дошла)
        await call.answer("Платёж подтверждён, выдаю ключ…")
        if payment.type == "unban":
            user = await repo.get_user_by_id(payment.user_id)
            await sub_service.unban_account(user)
            refreshed = await repo.get_user(user.tg_id)
            await _deliver_unban(call, refreshed)
        else:
            await _deliver_subscription(call, payment, bot)
        return
    if payment.status == "expired":
        await call.answer("Срок ожидания истёк. Создай новый платёж.", show_alert=True)
        return

    await call.answer("Проверяю оплату…")
    try:
        donation = await donation_alerts.find_matching(payment.code, payment.amount)
    except DonationAlertsError as e:
        log.error("DonationAlerts недоступен: %s", e)
        await call.message.answer(
            "⚠️ Не удалось проверить оплату. Попробуй через минуту или напиши в поддержку."
        )
        return

    if not donation:
        await call.message.answer(
            "⏳ <b>Оплата пока не найдена.</b>\n\n"
            "Проверь, что:\n"
            "• донат уже оплачен и прошёл;\n"
            f"• в сообщении к донату был код <code>{payment.code}</code>;\n"
            f"• сумма не меньше <b>{int(payment.amount)} ₽</b>.\n\n"
            "После оплаты подожди 1–2 минуты и нажми «Я оплатил» снова.",
            reply_markup=inline.payment_check(payment.id),
        )
        return

    # анти-дубль: этот донат уже использован?
    if await repo.da_id_used(donation.id):
        await call.message.answer(
            "⚠️ Этот платёж уже был зачтён ранее. Если думаешь, что это ошибка — напиши в поддержку.",
            reply_markup=inline.back_to_menu(),
        )
        return

    await repo.mark_payment_paid(payment.id, donation.id)

    if payment.type == "unban":
        user = await repo.get_user_by_id(payment.user_id)
        await sub_service.unban_account(user)
        # разбан включает Solo-подписку
        refreshed = await repo.get_user(user.tg_id)
        await _deliver_unban(call, refreshed)
        return

    await _deliver_subscription(call, payment, bot)


async def _deliver_unban(call: CallbackQuery, user) -> None:
    try:
        sub, _ = await sub_service.issue_or_extend(user, "solo")
    except sub_service.SubscriptionError as e:
        log.error("Разбан: ошибка выдачи Solo: %s", e)
        await call.message.answer(
            "✅ Аккаунт разбанен, но ключ не выдался. Напиши в поддержку.",
            reply_markup=inline.back_to_menu(),
        )
        return
    key = sub.vless_key or ""
    qr = BufferedInputFile(make_qr(key).read(), filename="key.png")
    await call.message.answer_photo(
        photo=qr,
        caption=(
            "✅ <b>Аккаунт разбанен!</b>\n\n"
            f"Подписка Solo активна до <b>{fmt_date(sub.expires_at)}</b>.\n\n"
            f"🔑 <code>{key}</code>"
        ),
        reply_markup=inline.my_sub_menu(),
    )
