"""operation_logs 操作日志表（文档 4.7）：只追加，保留不少于 1 年。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, RefId, now_local


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    operator_type: Mapped[str] = mapped_column(String(16), nullable=False)  # ADMIN / STUDENT / SYSTEM
    operator_id: Mapped[int | None] = mapped_column(RefId)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(RefId)
    detail: Mapped[dict | None] = mapped_column(JSON)  # 变更摘要（前后值）
    ip: Mapped[str | None] = mapped_column(String(45))
    ua: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=now_local)

    __table_args__ = (
        Index("idx_created_at", "created_at"),
        Index("idx_operator", "operator_id", "created_at"),
        Index("idx_target", "target_type", "target_id"),
    )
