"""会话、登录/签到尝试与操作日志模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, now_local


class AuthSession(Base, TimestampMixin):
    """登录会话：refresh token 摘要 + 会话生命周期（文档 2.2）。"""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)  # student / admin
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("ix_auth_sessions_account", "account_type", "account_id", "expires_at"),
    )

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > now_local()


class LoginAttempt(Base):
    """登录尝试，用于失败锁定（文档 3.1 / 3.6）。"""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)
    account: Mapped[str] = mapped_column(String(50), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, nullable=False, index=True)

    __table_args__ = (Index("ix_login_attempts_lookup", "account_type", "account", "created_at"),)


class CheckinAttempt(Base):
    """签到码尝试，用于连续输错锁定（文档 2.6 / 7.3.2）。"""

    __tablename__ = "checkin_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, nullable=False, index=True)

    __table_args__ = (Index("ix_checkin_attempts_lookup", "activity_id", "student_id", "created_at"),)


class OperationLog(Base):
    """操作日志（文档 5.12 / A-35）。"""

    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)   # student / admin / system
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_name: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, nullable=False, index=True)

    __table_args__ = (Index("ix_operation_logs_actor", "actor_type", "actor_id", "created_at"),)
