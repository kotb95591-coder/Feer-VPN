"""FSM-состояния. Текстовый ввод — для промокода, поддержки, пополнения и админ-визардов."""
from aiogram.fsm.state import State, StatesGroup


class PromoStates(StatesGroup):
    waiting_code = State()


class SupportStates(StatesGroup):
    waiting_message = State()


class TopupStates(StatesGroup):
    waiting_amount = State()


class AdminStates(StatesGroup):
    broadcast_text = State()
    # Визард создания промокода
    promo_code = State()
    promo_percent = State()
    promo_fixed = State()
    promo_bonus = State()
    promo_limit = State()
    # Визард выдачи подписки
    give_target = State()
    give_days = State()
    # Поиск клиента
    find_client = State()
    # Начисление баланса
    addbal_amount = State()
    # Ответ в поддержку
    reply_text = State()
