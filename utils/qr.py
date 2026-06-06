"""Генерация QR-кода для VLESS-ключа (возвращает PNG в памяти)."""
import io

import qrcode


def make_qr(data: str) -> io.BytesIO:
    """Создаёт PNG QR-кода и возвращает BytesIO (готов для aiogram BufferedInputFile)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "key.png"
    return buf
