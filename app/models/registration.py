"""registrations 报名表（文档 4.4）。

说明：
- 本表不做软删除（文档 2.5 / 4.4）：取消报名置 CANCELLED，再次报名**复用同一行**，
  重置 signup_at、清空 cancel_reason 与审核字段 —— 因此 uk_activity_user 始终生效。
- 冗余三个快照字段，统计按快照归属，避免学生转专业后历史数据漂移（文档 6.5）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import RegistrationStatus, SignupSource
from app.models.base import Base, BigId, RefId, TimestampMixin, now_local
from app.models.activity import Activity
from app.models.user import User


class Registration(Base, TimestampMixin):
    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(RefId, nullable=False)
    user_id: Mapped[int] = mapped_column(RefId, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RegistrationStatus.PENDING)

    # 报名时快照（文档 6.5）
    college_snapshot: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    major_snapshot: Mapped[str | None] = mapped_column(String(50))
    class_snapshot: Mapped[str | None] = mapped_column(String(50))

    signup_at: Mapped[datetime] = mapped_column(nullable=False, default=now_local)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SignupSource.SELF)

    audit_by: Mapped[int | None] = mapped_column(RefId)
    audit_at: Mapped[datetime | None] = mapped_column()
    audit_remark: Mapped[str | None] = mapped_column(String(200))

    checkin_at: Mapped[datetime | None] = mapped_column()
    checkin_method: Mapped[str | None] = mapped_column(String(16))  # CODE / MANUAL
    is_late: Mapped[bool] = mapped_column(nullable=False, default=False)
    cancel_reason: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        Index("uk_activity_user", "activity_id", "user_id", unique=True),
        Index("idx_user_status", "user_id", "status"),
        Index("idx_activity_status", "activity_id", "status"),
        Index("idx_checkin_at", "checkin_at"),
    )

    activity: Mapped[Activity] = relationship(
        primaryjoin="foreign(Registration.activity_id) == Activity.id",
        lazy="joined",
        viewonly=True,
    )
    user: Mapped[User] = relationship(
        primaryjoin="foreign(Registration.user_id) == User.id",
        lazy="joined",
        viewonly=True,
    )

    @property
    def is_checked_in(self) -> bool:
        return self.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)

    # 兼容旧代码命名
    @property
    def student(self) -> User:
        return self.user
