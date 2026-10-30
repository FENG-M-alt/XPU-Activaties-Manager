"""管理员账号模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccountStatus, AdminRole, DataScope
from app.models.base import Base, TimestampMixin


class Admin(Base, TimestampMixin):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=AdminRole.ADMIN, nullable=False)
    data_scope: Mapped[str] = mapped_column(String(16), default=DataScope.SELF, nullable=False)
    college: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default=AccountStatus.ACTIVE, nullable=False, index=True)
    is_first_login: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def is_super_admin(self) -> bool:
        return self.role == AdminRole.SUPER_ADMIN

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE
