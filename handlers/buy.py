"""Покупка подписки: выбор тарифа → (промокод) → оплата с баланса."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from config import config, get_tariff
from db import repo
from handlers import texts
from keyboards import inline
from services import promo as promo_service
from services import subscription as sub_service
from utils.helpers import fmt_date, plan_title
from utils.qr import make_qr
from utils.tg import edit_or_send, edit_or_send_media

log = logging.getLogger(__name__)
router = Router(name="buy")


@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    active = await repo.get_active_subscription(user.id)
    if active:
        await edit_or_send(
            call,
            f"🔑 У тебя уже есть активная подписка <b>{plan_title(active.plan)}</b> "
            f"до <b>{fmt_date(active.expires_at)}</b>.\n\n"
            "Продление происходит автоматически с баланса — покупать ещё одну не нужно. "
            "Просто держи баланс пополненным, и доступ не прервётся.",
            inline.my_sub_menu(),
        )
        await call.answer()
        return
    eligible = await sub_service.is_trial_eligible(user)
    await edit_or_send_media(
        call, texts.tariffs_text(), inline.tariffs_menu(show_trial=eligible), config.IMG_TARIFFS
    )
    await call.answer()


@router.callback_query(F.data == "trial")
async def cb_trial(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    active = await repo.get_active_subscription(user.id)
    if active:
        await edit_or_send(
            call,
            "🔑 У тебя уже есть активная подписка — пробный период не нужен.",
            inline.my_sub_menu(),
        )
        await call.answer()
        return
    if not await sub_service.is_trial_eligible(user):
        await edit_or_send(
            call,
            "🎁 Пробный период доступен только новым пользователям, которые ещё ни разу не оплачивали подписку.",
            inline.tariffs_menu(),
        )
        await call.answer()
        return
    try:
        sub = await sub_service.start_trial(user)
    except sub_service.TrialNotEligibleError:
        await edit_or_send(
            call,
            "🎁 Пробный период недоступен на твоём аккаунте.",
            inline.tariffs_menu(),
        )
        await call.answer()
        return
    except sub_service.SubscriptionError as e:
        log.error("Ошибка выдачи пробного периода: %s", e)
        await edit_or_send(
            call,
            "⚠️ Не удалось выдать пробный ключ. Попробуй позже или напиши в поддержку.",
            inline.back_to_menu(),
        )
        await call.answer()
        return
    await state.clear()
    tariff = get_tariff("trial")
    new_balance = await repo.get_balance(user.id)
    await _deliver_key(call, tariff, sub, new_balance)
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
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    balance = await repo.get_balance(user.id)
    text = (
        f"{tariff['emoji']} <b>{tariff['title']}</b>\n\n"
        f"Цена: <b>{tariff['price']} ₽</b> / {tariff['days']} дней\n"
        f"Подключения: <b>{tariff['desc']}</b>\n"
        f"💼 Баланс: <b>{balance:.0f} ₽</b>\n\n"
        "✅ С правилами ознакомлен(а).\n"
        "Оплата спишется с баланса. Можно ввести промокод или сразу оплатить."
    )
    await edit_or_send(call, text, inline.buy_confirm(plan, tariff["price"]))
    await call.answer()


async def _deliver_key(call: CallbackQuery, tariff: dict, sub, new_balance: float) -> None:
    """Отправляет ключ + QR после списания с баланса."""
    key = sub.vless_key or ""
    caption = (
        "✅ <b>Подписка активна!</b>\n\n"
        f"Тариф: <b>{tariff['title']}</b>\n"
        f"Действует до: <b>{fmt_date(sub.expires_at)}</b>\n"
        f"Подключения: <b>{tariff['desc']}</b>\n"
        f"💼 Остаток на балансе: <b>{new_balance:.0f} ₽</b>\n\n"
        "🔑 <b>Твой ключ</b> (нажми, чтобы скопировать):\n"
        f"<code>{key}</code>\n\n"
        "Отсканируй QR или скопируй ключ в приложение."
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


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(call: CallbackQuery, state: FSMContext) -> None:
    plan = call.data.split(":", 1)[1]
    data = await state.get_data()
    tariff = get_tariff(plan)
    if not tariff:
        await call.answer("Неизвестный тариф", show_alert=True)
        return

    price = float(data.get("price", tariff["price"]))
    bonus_days = int(data.get("bonus_days", 0) or 0)
    promocode = data.get("promocode")

    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)

    active = await repo.get_active_subscription(user.id)
    if active:
        await edit_or_send(
            call,
            "🔑 У тебя уже есть активная подписка — продление автоматическое с баланса. "
            "Покупать ещё одну не нужно.",
            inline.my_sub_menu(),
        )
        await call.answer()
        return

    balance = await repo.get_balance(user.id)

    if balance < price:
        need = price - balance
        await edit_or_send(
            call,
            f"💼 На балансе <b>{balance:.0f} ₽</b>, а для покупки "
            f"<b>{tariff['title']}</b> нужно <b>{price:.0f} ₽</b>.\n\n"
            f"Не хватает <b>{need:.0f} ₽</b> — пополни баланс и вернись к покупке.",
            inline.need_topup_menu(),
        )
        await call.answer()
        return

    try:
        sub, _is_new = await sub_service.buy_with_balance(user, plan, price, bonus_days)
    except sub_service.InsufficientFundsError:
        await edit_or_send(
            call,
            "💼 На балансе недостаточно средств. Пополни кабинет и попробуй снова.",
            inline.need_topup_menu(),
        )
        await call.answer()
        return
    except sub_service.SubscriptionError as e:
        log.error("Ошибка выдачи подписки с баланса: %s", e)
        await edit_or_send(
            call,
            "⚠️ Деньги не списаны: не удалось выдать ключ. Напиши в поддержку — мы всё решим.",
            inline.back_to_menu(),
        )
        await call.answer()
        return

    # промокод успешно применён — фиксируем редемпцию
    if promocode:
        result = await promo_service.validate_and_apply(
            promocode, user.id, int(tariff["price"]), plan=plan
        )
        if result.ok and result.promo:
            await promo_service.redeem(result.promo.id, user.id)

    await state.clear()
    new_balance = await repo.get_balance(user.id)
    await _deliver_key(call, tariff, sub, new_balance)
    await call.answer()
