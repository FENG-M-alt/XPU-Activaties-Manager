"""报名记录模型。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import RegistrationStatus, SignupSource
from app.models.base import Base, TimestampMixin, now_local
from app.models.activity import Activity
from app.models.student import Student


class Registration(Base, TimestampMixin):
    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), default=RegistrationStatus.PENDING, nullable=False, index=True)
    signup_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(8), default=SignupSource.SELF, nullable=False)

    audit_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))
    audit_at: Mapped[datetime | None] = mapped_column(DateTime)
    audit_remark: Mapped[str | None] = mapped_column(String(500))

    checkin_at: Mapped[datetime | None] = mapped_column(DateTime)
    checkin_method: Mapped[str | None] = mapped_column(String(8))
    checkin_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))
    checkin_reason: Mapped[str | None] = mapped_column(String(500))
    checkin_ip: Mapped[str | None] = mapped_column(String(64))
    is_late: Mapped[bool] = mapped_column(default=False, nullable=False)
    hours_awarded: Mapped[Decimal] = mapped_column(Numeric(4, 1), default=Decimal("0.0"), nullable=False)

    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))

    activity: Mapped[Activity] = relationship(lazy="joined")
    student: Mapped[Student] = relationship(lazy="joined")

    __table_args__ = (
        # 同一学生对同一活动仅一条有效记录；CANCELLED / REJECTED 不占用唯一性（文档 7.1.5）
        Index(
            "uq_registrations_active",
            "activity_id",
            "student_id",
            unique=True,
            sqlite_where=text("status NOT IN ('CANCELLED', 'REJECTED')"),
            postgresql_where=text("status NOT IN ('CANCELLED', 'REJECTED')"),
        ),
        Index("ix_registrations_activity_status", "activity_id", "status"),
        Index("ix_registrations_student_status", "student_id", "status"),
    )

    @property
    def is_checked_in(self) -> bool:
        return self.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)
