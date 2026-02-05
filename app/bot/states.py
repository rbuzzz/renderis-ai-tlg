from aiogram.fsm.state import State, StatesGroup


class GenerateFlow(StatesGroup):
    choosing_model = State()
    entering_prompt = State()
    choosing_options = State()
    choosing_outputs = State()
    confirming = State()


class AdminFlow(StatesGroup):
    setting_price = State()
    bulk_multiplier = State()
    create_referral = State()
    create_promo = State()
    grant_credits = State()
    ban_user = State()
