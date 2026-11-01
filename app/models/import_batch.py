"""import_batches 导入批次表（文档 4.6）。

失败明细不落库，写入文件后把地址存入 error_file_url（文档 4.6）；
待确认的解析结果保存在进程内（见 app/services/importer.py，30 分钟 TTL）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ImportResult
from app.models.base import Base, BigId, IntU, RefId, now_local


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operator_id: Mapped[int] = mapped_column(RefId, nullable=False)
    total: Mapped[int] = mapped_column(IntU, nullable=False, default=0)
    success: Mapped[int] = mapped_column(IntU, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(IntU, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ImportResult.SUCCESS)
    error_file_url: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=now_local)

    __table_args__ = (Index("idx_created_at", "created_at"),)

    @property
    def batch_id(self) -> int:
        """对外批次标识（数据库主键）。"""
        return self.id
