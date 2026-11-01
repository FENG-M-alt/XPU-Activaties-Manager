"""补充表：官方 9 张表之外、接口契约必需但《数据库设计说明书》未建模的表。

集中放在本模块，便于评审与后续决策（可随时降级为内存/文件实现）：

| 表 | 为什么需要 | 对应接口 |
| --- | --- | --- |
| `auth_sessions` | 刷新令牌需要服务端状态才能撤销；文档 2.2 要求同账号最多 3 个会话、改密后其他会话失效 | U-01/U-02/U-03/U-04 |
| `notices` | 公告需要持久化与有效期；文档 4.11/U-17 要求返回生效中公告，而库设计仅在 dict_items 中留有 notice_type | U-07/U-17 |
| `export_tasks` | 异步导出需要任务记录与 7 天有效期；文档 A-32 要求凭 id 下载 | A-21/A-32 |
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ExportTaskStatus
from app.models.base import Base, BigId, RefId, TimestampMixin, IntU, now_local


class AuthSession(Base, TimestampMixin):
    """登录会话（补充表）：refresh token 仅存摘要，可撤销。"""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user / admin
    account_id: Mapped[int] = mapped_column(RefId, nullable=False)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(nullable=False, default=now_local)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column()
    last_used_at: Mapped[datetime | None] = mapped_column()
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("idx_account", "account_type", "account_id", "expires_at"),
        Index("idx_refresh_hash", "refresh_hash"),
    )

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > now_local()


class Notice(Base, TimestampMixin):
    """公告（补充表）。到期公告自动下线（文档 4.11）。"""

    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(String(4000), nullable=False, default="")
    notice_type: Mapped[str] = mapped_column(String(32), nullable=False, default="system")  # dict_items.notice_type
    is_pinned: Mapped[bool] = mapped_column(nullable=False, default=False)
    start_at: Mapped[datetime | None] = mapped_column()
    end_at: Mapped[datetime | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(RefId)
    deleted_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (Index("idx_active_time", "is_active", "start_at", "end_at"),)

    def is_effective(self, now: datetime | None = None) -> bool:
        now = now or now_local()
        if not self.is_active or self.deleted_at is not None:
            return False
        if self.start_at and now < self.start_at:
            return False
        if self.end_at and now > self.end_at:
            return False
        return True


class ExportTask(Base):
    """异步导出任务（补充表）：文件保留 7 天，过期返回 40400。"""

    __tablename__ = "export_tasks"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    activity_id: Mapped[int | None] = mapped_column(RefId)
    export_type: Mapped[str] = mapped_column(String(30), nullable=False, default="registrations")
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_path: Mapped[str | None] = mapped_column(String(500))
    row_count: Mapped[int] = mapped_column(IntU, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ExportTaskStatus.PENDING)
    error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(RefId)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=now_local)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (Index("idx_created_by", "created_by", "created_at"),)
