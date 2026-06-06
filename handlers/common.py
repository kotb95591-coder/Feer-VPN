"""Старт, главное меню и навигация."""
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import config
from db import repo
from handlers import texts
from handlers.states import SupportStates
from keyboards import inline
from utils.tg import edit_or_send, edit_or_send_media, send_screen

log = logging.getLogger(__name__)
router = Router(name="common")


async def _show_menu(target: Message | CallbackQuery, user) -> None:
    is_admin = config.is_admin(user.tg_id)
    has_sub = bool(await repo.get_active_subscription(user.id))
    text = texts.main_menu_text()
    kb = inline.main_menu(is_admin=is_admin, has_sub=has_sub)
    if isinstance(target, CallbackQuery):
        await edit_or_send_media(target, text, kb, config.IMG_WELCOME)
        await target.answer()
    else:
        await send_screen(target, text, kb, config.IMG_WELCOME)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    # реферальный код из deep-link: /start REF12345
    referrer_id = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].upper().startswith("REF"):
        try:
            ref = int(parts[1][3:])
            if ref != message.from_user.id:
                referrer_id = ref
        except ValueError:
            pass

    user = await repo.get_or_create_user(
        message.from_user.id, message.from_user.username, referrer_id
    )

    if user.status == "banned":
        await message.answer(
            texts.banned_text(user.ban_reason), reply_markup=inline.banned_menu()
        )
        return

    await send_screen(
        message,
        texts.welcome(message.from_user.first_name or "друг"),
        inline.main_menu(
            is_admin=config.is_admin(user.tg_id),
            has_sub=bool(await repo.get_active_subscription(user.id)),
        ),
        config.IMG_WELCOME,
    )


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_or_create_user(call.from_user.id, call.from_user.username)
    if user.status == "banned":
        await edit_or_send(call, texts.banned_text(user.ban_reason), inline.banned_menu())
        await call.answer()
        return
    await _show_menu(call, user)


@router.callback_query(F.data == "howto")
async def cb_howto(call: CallbackQuery) -> None:
    await edit_or_send_media(call, texts.howto_text(), inline.back_to_menu(), config.IMG_HOWTO)
    await call.answer()


@router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupportStates.waiting_message)
    await edit_or_send(call, texts.support_text(), inline.cancel_kb())
    await call.answer()


@router.message(SupportStates.waiting_message)
async def support_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    uname = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else (message.from_user.full_name or "без имени")
    )
    body = message.text or message.caption or "(пустое сообщение)"
    sent = 0
    for admin_id in config.ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                "🆘 <b>Новое сообщение в поддержку</b>\n"
                f"От: {uname} · tg:<code>{message.from_user.id}</code>\n\n"
                f"{body}\n\n"
                f"Ответить: <code>/reply {message.from_user.id} текст</code>",
            )
            sent += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось доставить сообщение админу %s: %s", admin_id, e)
    if sent:
        await message.answer(
            "✅ Сообщение отправлено в поддержку. Мы ответим как можно скорее.",
            reply_markup=inline.back_to_menu(),
        )
    else:
        await message.answer(
            "⚠️ Поддержка временно недоступна. Попробуй позже.",
            reply_markup=inline.back_to_menu(),
        )
