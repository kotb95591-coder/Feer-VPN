"""Сборка всех роутеров."""
from aiogram import Dispatcher

from handlers import admin, buy, common, payment, promo, subscription, wallet


def setup_routers(dp: Dispatcher) -> None:
    # порядок важен: common (start/menu) первым, FSM-роутеры после
    dp.include_router(common.router)
    dp.include_router(buy.router)
    dp.include_router(promo.router)
    dp.include_router(payment.router)
    dp.include_router(subscription.router)
    dp.include_router(wallet.router)
    dp.include_router(admin.router)
