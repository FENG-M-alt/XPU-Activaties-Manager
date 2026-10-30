"""活动与活动类型模型。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ActivityStatus
from app.models.admin import Admin
from app.models.base import Base, TimestampMixin


class ActivityType(Base, TimestampMixin):
    __tablename__ = "activity_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), unique=True)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Activity(Base, TimestampMixin):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    type_id: Mapped[int | None] = mapped_column(ForeignKey("activity_types.id"), index=True)
    host: Mapped[str | None] = mapped_column(String(100))
    organizer: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(200))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    notice: Mapped[str | None] = mapped_column(Text)

    contact_name: Mapped[str | None] = mapped_column(String(50))
    contact_phone: Mapped[str | None] = mapped_column(String(50))

    signup_start: Mapped[datetime | None] = mapped_column(DateTime)
    signup_end: Mapped[datetime | None] = mapped_column(DateTime)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    capacity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    need_audit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_cancel: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)
    checkin_before: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    checkin_after: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # 所属学院：用于管理员数据范围 COLLEGE 过滤（文档 2.7）
    college: Mapped[str | None] = mapped_column(String(100), index=True)

    status: Mapped[str] = mapped_column(String(16), default=ActivityStatus.DRAFT, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    registered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    checkin_code: Mapped[str | None] = mapped_column(String(6), index=True)
    checkin_code_generated_at: Mapped[datetime | None] = mapped_column(DateTime)
    checkin_code_reset_at: Mapped[datetime | None] = mapped_column(DateTime)
    share_token: Mapped[str | None] = mapped_column(String(12), unique=True, index=True)
    share_expire_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    delete_reason: Mapped[str | None] = mapped_column(String(500))

    type: Mapped[ActivityType | None] = relationship(lazy="joined")
    creator: Mapped[Admin | None] = relationship(foreign_keys=[created_by], lazy="joined")

    __table_args__ = (
        Index("ix_activities_status_start", "status", "start_time"),
        Index("ix_activities_deleted_status", "is_deleted", "status"),
    )

    @property
    def remaining(self) -> int:
        return max(self.capacity - self.registered_count, 0)

    @property
    def checkin_window(self) -> tuple[datetime | None, datetime | None]:
        if not self.start_time or not self.end_time:
            return None, None
        from datetime import timedelta

        return (
            self.start_time - timedelta(minutes=self.checkin_before),
            self.end_time + timedelta(minutes=self.checkin_after),
        )


class ActivityCheckinCode(Base, TimestampMixin):
    """签到码历史：用于区分"旧码已失效"(40910) 与"码错误"(40905)。"""

    __tablename__ = "activity_checkin_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    reset_reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))
