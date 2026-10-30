"""学生账号模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccountStatus
from app.models.base import Base, TimestampMixin


class Student(Base, TimestampMixin):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    college: Mapped[str | None] = mapped_column(String(100), index=True)
    major: Mapped[str | None] = mapped_column(String(100))
    class_name: Mapped[str | None] = mapped_column(String(100))
    grade: Mapped[str | None] = mapped_column(String(20), index=True)
    mobile: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=AccountStatus.ACTIVE, nullable=False, index=True)
    is_first_login: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    total_hours: Mapped[float] = mapped_column(default=0.0, nullable=False)
    total_activities: Mapped[int] = mapped_column(default=0, nullable=False)

    __table_args__ = (Index("ix_students_college_grade", "college", "grade"),)

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE
