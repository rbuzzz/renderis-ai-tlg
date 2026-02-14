from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminChangeComment, AdminChangeRequest, PromoCode, User
from app.services.credits import CreditsService
from app.utils.credits import to_credits
from app.utils.time import utcnow


CHANGE_ADD_CREDITS = "add_credits"
CHANGE_SUBTRACT_CREDITS = "subtract_credits"
CHANGE_SET_BALANCE = "set_balance"
CHANGE_REVOKE_PROMO = "revoke_promo"

CHANGE_TYPES = {
    CHANGE_ADD_CREDITS,
    CHANGE_SUBTRACT_CREDITS,
    CHANGE_SET_BALANCE,
    CHANGE_REVOKE_PROMO,
}

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_NEEDS_INFO = "needs_info"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_APPLIED = "applied"

ACTIVE_STATUSES = {STATUS_DRAFT, STATUS_PENDING, STATUS_NEEDS_INFO}


@dataclass
class ValidationResult:
    ok: bool
    message: str = ""


class ChangeRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.credits = CreditsService(session)

    async def validate_change(
        self,
        *,
        change_type: str,
        user: User,
        credits_amount: int | None,
        balance_value: int | None,
        promo_code: str | None,
    ) -> ValidationResult:
        if change_type not in CHANGE_TYPES:
            return ValidationResult(False, "unknown_change_type")

        if change_type in {CHANGE_ADD_CREDITS, CHANGE_SUBTRACT_CREDITS}:
            if credits_amount is None or credits_amount <= 0:
                return ValidationResult(False, "invalid_credits_amount")

        if change_type == CHANGE_SET_BALANCE:
            if balance_value is None or balance_value < 0:
                return ValidationResult(False, "invalid_balance_value")

        if change_type == CHANGE_REVOKE_PROMO:
            code = (promo_code or "").strip().upper()
            if not code:
                return ValidationResult(False, "invalid_promo_code")
            row = await self.session.execute(
                select(PromoCode).where(
                    PromoCode.code == code,
                    PromoCode.redeemed_by_user_id == user.id,
                )
            )
            promo = row.scalar_one_or_none()
            if not promo:
                return ValidationResult(False, "promo_not_found")
            if not promo.active:
                return ValidationResult(False, "promo_already_revoked")

        return ValidationResult(True)

    async def create_draft(
        self,
        *,
        change_type: str,
        user: User,
        reason: str,
        created_by_login: str,
        created_by_role: str,
        credits_amount: int | None = None,
        balance_value: int | None = None,
        promo_code: str | None = None,
    ) -> tuple[AdminChangeRequest | None, str | None]:
        validation = await self.validate_change(
            change_type=change_type,
            user=user,
            credits_amount=credits_amount,
            balance_value=balance_value,
            promo_code=promo_code,
        )
        if not validation.ok:
            return None, validation.message

        now = utcnow()
        req = AdminChangeRequest(
            status=STATUS_DRAFT,
            change_type=change_type,
            target_user_id=user.id,
            credits_amount=credits_amount,
            balance_value=balance_value,
            promo_code=((promo_code or "").strip().upper() or None),
            reason=reason.strip(),
            created_by_role=created_by_role,
            created_by_login=created_by_login.strip() or "subadmin",
            created_at=now,
            updated_at=now,
        )
        self.session.add(req)
        await self.session.flush()
        return req, None

    async def add_comment(
        self,
        *,
        req: AdminChangeRequest,
        author_role: str,
        author_login: str,
        author_telegram_id: int | None,
        message: str,
    ) -> AdminChangeComment:
        comment = AdminChangeComment(
            request_id=req.id,
            author_role=author_role,
            author_login=author_login.strip() or author_role,
            author_telegram_id=author_telegram_id,
            message=message.strip(),
            created_at=utcnow(),
        )
        req.updated_at = utcnow()
        self.session.add(comment)
        return comment

    async def submit(self, req: AdminChangeRequest) -> tuple[bool, str | None]:
        if req.status not in {STATUS_DRAFT, STATUS_NEEDS_INFO}:
            return False, "wrong_status"
        req.status = STATUS_PENDING
        req.submitted_at = utcnow()
        req.updated_at = utcnow()
        return True, None

    async def cancel(self, req: AdminChangeRequest) -> tuple[bool, str | None]:
        if req.status not in ACTIVE_STATUSES:
            return False, "wrong_status"
        req.status = STATUS_CANCELLED
        req.updated_at = utcnow()
        return True, None

    async def mark_needs_info(
        self,
        req: AdminChangeRequest,
        *,
        reviewer_login: str,
        reviewer_telegram_id: int | None = None,
    ) -> tuple[bool, str | None]:
        if req.status != STATUS_PENDING:
            return False, "wrong_status"
        req.status = STATUS_NEEDS_INFO
        req.reviewed_at = utcnow()
        req.reviewed_by_login = reviewer_login
        req.reviewed_by_telegram_id = reviewer_telegram_id
        req.updated_at = utcnow()
        return True, None

    async def reject(
        self,
        req: AdminChangeRequest,
        *,
        reviewer_login: str,
        reviewer_telegram_id: int | None = None,
    ) -> tuple[bool, str | None]:
        if req.status not in {STATUS_PENDING, STATUS_NEEDS_INFO}:
            return False, "wrong_status"
        req.status = STATUS_REJECTED
        req.reviewed_at = utcnow()
        req.reviewed_by_login = reviewer_login
        req.reviewed_by_telegram_id = reviewer_telegram_id
        req.updated_at = utcnow()
        return True, None

    async def apply_request(
        self,
        req: AdminChangeRequest,
        *,
        reviewer_login: str,
        reviewer_telegram_id: int | None = None,
    ) -> tuple[bool, str | None]:
        if req.status not in {STATUS_PENDING, STATUS_NEEDS_INFO}:
            return False, "wrong_status"

        user = await self.session.get(User, req.target_user_id)
        if not user:
            return False, "user_not_found"

        if req.change_type == CHANGE_ADD_CREDITS:
            amount = int(req.credits_amount or 0)
            if amount <= 0:
                return False, "invalid_credits_amount"
            await self.credits.add_ledger(
                user,
                amount,
                "subadmin_request_add",
                meta={
                    "request_id": req.id,
                    "author_login": req.created_by_login,
                    "reason": req.reason,
                },
            )
        elif req.change_type == CHANGE_SUBTRACT_CREDITS:
            amount = int(req.credits_amount or 0)
            if amount <= 0:
                return False, "invalid_credits_amount"
            if to_credits(user.balance_credits) < to_credits(amount):
                return False, "insufficient_balance"
            await self.credits.add_ledger(
                user,
                -amount,
                "subadmin_request_subtract",
                meta={
                    "request_id": req.id,
                    "author_login": req.created_by_login,
                    "reason": req.reason,
                },
            )
        elif req.change_type == CHANGE_SET_BALANCE:
            target_balance = int(req.balance_value if req.balance_value is not None else -1)
            if target_balance < 0:
                return False, "invalid_balance_value"
            delta = to_credits(target_balance) - to_credits(user.balance_credits)
            if delta != 0:
                await self.credits.add_ledger(
                    user,
                    delta,
                    "subadmin_request_set_balance",
                    meta={
                        "request_id": req.id,
                        "author_login": req.created_by_login,
                        "reason": req.reason,
                        "target_balance": target_balance,
                    },
                )
        elif req.change_type == CHANGE_REVOKE_PROMO:
            code = (req.promo_code or "").strip().upper()
            if not code:
                return False, "invalid_promo_code"
            row = await self.session.execute(
                select(PromoCode).where(
                    PromoCode.code == code,
                    PromoCode.redeemed_by_user_id == user.id,
                )
            )
            promo = row.scalar_one_or_none()
            if not promo:
                return False, "promo_not_found"
            if not promo.active:
                return False, "promo_already_revoked"

            promo.active = False
            credits = int(promo.credits_amount or 0)
            if credits > 0 and to_credits(user.balance_credits) >= to_credits(credits):
                await self.credits.add_ledger(
                    user,
                    -credits,
                    "subadmin_request_promo_revoke",
                    meta={
                        "request_id": req.id,
                        "code": code,
                        "author_login": req.created_by_login,
                        "reason": req.reason,
                    },
                )
        else:
            return False, "unknown_change_type"

        now = utcnow()
        req.status = STATUS_APPLIED
        req.reviewed_at = now
        req.applied_at = now
        req.reviewed_by_login = reviewer_login
        req.reviewed_by_telegram_id = reviewer_telegram_id
        req.apply_error = None
        req.updated_at = now
        return True, None
