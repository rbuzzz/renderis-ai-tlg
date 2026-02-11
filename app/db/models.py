from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    balance_credits: Mapped[int] = mapped_column(Integer, default=0)
    referral_discount_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    referral_code_applied: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    ledger_entries: Mapped[list['CreditLedger']] = relationship(back_populates='user')
    generations: Mapped[list['Generation']] = relationship(back_populates='user')


class CreditLedger(Base):
    __tablename__ = 'credit_ledger'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    delta_credits: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped['User'] = relationship(back_populates='ledger_entries')


class Price(Base):
    __tablename__ = 'prices'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_key: Mapped[str] = mapped_column(String(64), index=True)
    option_key: Mapped[str] = mapped_column(String(64), index=True)
    price_credits: Mapped[int] = mapped_column(Integer)
    provider_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    model_type: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint('model_key', 'option_key', name='uq_prices_model_option'),
    )


class AppSetting(Base):
    __tablename__ = 'app_settings'

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReferralCode(Base):
    __tablename__ = 'referral_codes'

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    discount_pct: Mapped[int] = mapped_column(Integer)
    created_by_admin_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class ReferralUse(Base):
    __tablename__ = 'referral_uses'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey('referral_codes.code'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PromoCode(Base):
    __tablename__ = 'promo_codes'

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    credits_amount: Mapped[int] = mapped_column(Integer)
    created_by_admin_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    redeemed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class StarProduct(Base):
    __tablename__ = 'star_products'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    stars_amount: Mapped[int] = mapped_column(Integer)
    credits_amount: Mapped[int] = mapped_column(Integer)
    credits_base: Mapped[int] = mapped_column(Integer, default=0)
    credits_bonus: Mapped[int] = mapped_column(Integer, default=0)
    price_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = 'orders'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(128), unique=True)
    provider_payment_charge_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[str] = mapped_column(String(128))
    stars_amount: Mapped[int] = mapped_column(Integer)
    credits_amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Generation(Base):
    __tablename__ = 'generations'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    outputs_requested: Mapped[int] = mapped_column(Integer)
    total_cost_credits: Mapped[int] = mapped_column(Integer)
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    final_cost_credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped['User'] = relationship(back_populates='generations')
    tasks: Mapped[list['GenerationTask']] = relationship(back_populates='generation')


class GenerationTask(Base):
    __tablename__ = 'generation_tasks'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[int] = mapped_column(ForeignKey('generations.id'), index=True)
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(16))
    result_urls: Mapped[list | None] = mapped_column(JSONB, default=list)
    fail_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fail_msg: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    generation: Mapped['Generation'] = relationship(back_populates='tasks')


class SupportThread(Base):
    __tablename__ = 'support_threads'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default='open')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped['User'] = relationship()
    messages: Mapped[list['SupportMessage']] = relationship(back_populates='thread')


class SupportMessage(Base):
    __tablename__ = 'support_messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey('support_threads.id'), index=True)
    sender_type: Mapped[str] = mapped_column(String(16))
    sender_admin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    thread: Mapped['SupportThread'] = relationship(back_populates='messages')


class AdminChangeRequest(Base):
    __tablename__ = "admin_change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="draft")
    change_type: Mapped[str] = mapped_column(String(32), index=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    credits_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    balance_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promo_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    created_by_role: Mapped[str] = mapped_column(String(16), default="subadmin")
    created_by_login: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    apply_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_user: Mapped["User"] = relationship()
    comments: Mapped[list["AdminChangeComment"]] = relationship(back_populates="request")


class AdminChangeComment(Base):
    __tablename__ = "admin_change_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("admin_change_requests.id"), index=True)
    author_role: Mapped[str] = mapped_column(String(16))
    author_login: Mapped[str] = mapped_column(String(255))
    author_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    request: Mapped["AdminChangeRequest"] = relationship(back_populates="comments")
