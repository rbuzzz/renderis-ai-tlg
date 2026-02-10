from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SupportMessage, SupportThread, User
from app.utils.time import utcnow


class SupportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_thread(self, thread_id: int) -> Optional[SupportThread]:
        return await self.session.get(SupportThread, thread_id)

    async def get_thread_by_user(self, user_id: int) -> Optional[SupportThread]:
        result = await self.session.execute(select(SupportThread).where(SupportThread.user_id == user_id))
        return result.scalar_one_or_none()

    async def ensure_thread(self, user: User) -> SupportThread:
        now = utcnow()
        thread = await self.get_thread_by_user(user.id)
        if thread:
            thread.updated_at = now
            return thread
        thread = SupportThread(
            user_id=user.id,
            status='open',
            created_at=now,
            updated_at=now,
            last_message_at=now,
        )
        self.session.add(thread)
        await self.session.flush()
        return thread

    async def add_message(
        self,
        thread: SupportThread,
        sender_type: str,
        text: str,
        sender_admin_id: int | None = None,
        tg_message_id: int | None = None,
        media_type: str | None = None,
        media_path: str | None = None,
        media_file_name: str | None = None,
        media_mime_type: str | None = None,
    ) -> SupportMessage:
        now = utcnow()
        message = SupportMessage(
            thread_id=thread.id,
            sender_type=sender_type,
            sender_admin_id=sender_admin_id,
            text=text,
            media_type=media_type,
            media_path=media_path,
            media_file_name=media_file_name,
            media_mime_type=media_mime_type,
            tg_message_id=tg_message_id,
            created_at=now,
        )
        thread.last_message_at = now
        thread.updated_at = now
        self.session.add(message)
        return message
