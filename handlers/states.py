"""FSM-состояния. Текстовый ввод используется ТОЛЬКО для промокодов и админки."""
from aiogram.fsm.state import State, StatesGroup


class PromoStates(StatesGroup):
    waiting_code = State()


class SupportStates(StatesGroup):
    waiting_message = State()


class AdminStates(StatesGroup):
    promo_code = State()
    promo_type = State()
    promo_value = State()
    broadcast_text = State()
    extend_days = State()
