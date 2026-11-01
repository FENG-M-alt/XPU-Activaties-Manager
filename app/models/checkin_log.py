"""checkin_logs 签到流水表（文档 4.5）：只追加，不修改不删除。

学生自助签名为 CHECKIN + CODE；管理员补签为 CHECKIN + MANUAL；
撤销签到写入一条 UNDO 记录，并把 registrations 的签到字段回退，两处同时保留痕迹。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, RefId, now_local


class CheckinLog(Base):
    __tablename__ = "checkin_logs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    registration_id: Mapped[int] = mapped_column(RefId, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # CHECKIN / UNDO
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # CODE / MANUAL
    operator_id: Mapped[int | None] = mapped_column(RefId)  # 补签或撤销的操作人，学生自助为空
    reason: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=now_local)

    __table_args__ = (
        Index("idx_registration", "registration_id"),
        Index("idx_created_at", "created_at"),
    )
