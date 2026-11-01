"""users 学生账号表（文档 4.1）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccountStatus
from app.models.base import Base, BigId, RefId, SmallIntU, TimestampMixin, TinyIntU, SoftDeleteMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    college: Mapped[str] = mapped_column(String(50), nullable=False)
    major: Mapped[str | None] = mapped_column(String(50))
    class_name: Mapped[str | None] = mapped_column(String(50))
    grade: Mapped[int | None] = mapped_column(SmallIntU)  # 入学年份，如 2024（SMALLINT UNSIGNED）
    mobile: Mapped[str | None] = mapped_column(String(20))
    id_card_enc: Mapped[str | None] = mapped_column(String(190))  # AES-256-GCM 密文
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AccountStatus.ACTIVE)
    is_first_login: Mapped[bool] = mapped_column(default=True, nullable=False)
    import_batch_id: Mapped[int | None] = mapped_column(RefId)

    # 登录失败计数与锁定（文档 4.1.1 / 1.2 差异说明 2）
    login_fail_count: Mapped[int] = mapped_column(TinyIntU, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column()
    last_login_at: Mapped[datetime | None] = mapped_column()
    last_login_ip: Mapped[str | None] = mapped_column(String(45))

    __table_args__ = (
        Index("uk_student_no", "student_no", unique=True),
        Index("idx_college", "college"),
        Index("idx_status", "status"),
        Index("idx_import_batch", "import_batch_id"),
    )

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE


# 兼容旧代码中的 Student 名称（表已按文档改为 users）
Student = User
