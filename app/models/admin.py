"""admins 管理员账号表（文档 4.2）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccountStatus, AdminRole, DataScope
from app.models.base import Base, BigId, RefId, TimestampMixin, TinyIntU, SoftDeleteMixin


class Admin(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=AdminRole.ADMIN)
    data_scope: Mapped[str] = mapped_column(String(16), nullable=False, default=DataScope.SELF)
    college: Mapped[str | None] = mapped_column(String(50))  # data_scope=COLLEGE 时必填
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AccountStatus.ACTIVE)
    is_first_login: Mapped[bool] = mapped_column(default=True, nullable=False)

    login_fail_count: Mapped[int] = mapped_column(TinyIntU, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column()
    last_login_at: Mapped[datetime | None] = mapped_column()
    last_login_ip: Mapped[str | None] = mapped_column(String(45))
    created_by: Mapped[int | None] = mapped_column(RefId)

    __table_args__ = (
        Index("uk_username", "username", unique=True),
        Index("idx_role_status", "role", "status"),
    )

    @property
    def is_super_admin(self) -> bool:
        return self.role == AdminRole.SUPER_ADMIN

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE
