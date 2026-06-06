"""Вспомогательные функции для работы с сообщениями Telegram."""
import os
from contextlib import suppress

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message


async def edit_or_send(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Безопасно обновляет сообщение под inline-кнопкой.

    Если исходное сообщение — медиа (фото/QR с подписью, без text),
    то edit_text невозможен — удаляем старое и шлём новое сообщение.
    """
    msg = call.message
    if msg is None:
        return
    # сообщение с медиа не имеет text — редактировать нечего
    if msg.text is None:
        with suppress(TelegramBadRequest):
            await msg.delete()
        await msg.answer(text, reply_markup=reply_markup)
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        # на всякий случай (напр. "message is not modified" или старое медиа)
        with suppress(TelegramBadRequest):
            await msg.delete()
        await msg.answer(text, reply_markup=reply_markup)


def photo_input(src: str | None):
    """Превращает источник картинки в объект для отправки.

    - пусто / нет файла → None (бот покажет обычный текст)
    - http(s) URL → строка-ссылка
    - локальный путь → FSInputFile
    - иначе → считаем, что это Telegram file_id
    """
    if not src:
        return None
    if src.startswith(("http://", "https://")):
        return src
    if os.path.exists(src):
        return FSInputFile(src)
    return src


async def send_screen(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    image: str | None = None,
) -> None:
    """Шлёт новое сообщение: фото с подписью, если есть картинка, иначе текст."""
    photo = photo_input(image)
    if photo is not None:
        try:
            await message.answer_photo(photo, caption=text, reply_markup=reply_markup)
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=reply_markup)


async def edit_or_send_media(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    image: str | None = None,
) -> None:
    """Обновляет экран под кнопкой. Если задана картинка — шлёт фото с подписью."""
    msg = call.message
    if msg is None:
        return
    photo = photo_input(image)
    if photo is None:
        await edit_or_send(call, text, reply_markup)
        return
    # с картинкой проще всего: удалить старое и отправить новое фото
    with suppress(TelegramBadRequest):
        await msg.delete()
    try:
        await msg.answer_photo(photo, caption=text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await msg.answer(text, reply_markup=reply_markup)
