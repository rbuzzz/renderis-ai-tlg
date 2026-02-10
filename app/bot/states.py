from aiogram.fsm.state import State, StatesGroup


class GenerateFlow(StatesGroup):
    choosing_model = State()
    choosing_ref_mode = State()
    entering_prompt = State()
    choosing_options = State()
    collecting_refs = State()
    confirming = State()


class AdminFlow(StatesGroup):
    setting_price = State()
    bulk_multiplier = State()
    create_referral = State()
    create_promo = State()
    grant_credits = State()
    ban_user = State()
    support_reply = State()


class TopUpFlow(StatesGroup):
    entering_promo_code = State()
