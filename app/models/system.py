"""字典、系统参数、导入批次与导出任务模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ExportTaskStatus, ImportResult
from app.models.base import Base, TimestampMixin, now_local


class DictItem(Base, TimestampMixin):
    """通用字典项（文档 A-33）。"""

    __tablename__ = "dict_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dict_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class SystemSetting(Base):
    """系统参数（文档 A-34）。"""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(500))
    default_value: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(500))
    group: Mapped[str] = mapped_column(String(50), default="general", nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local, nullable=False)


class ImportBatch(Base, TimestampMixin):
    """学生导入批次，解析结果保留 30 分钟待确认（文档 5.10）。"""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ImportResult.SUCCESS, nullable=False)
    rows: Mapped[list | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)
    password_list: Mapped[list | None] = mapped_column(JSON)
    duplicate_strategy: Mapped[str | None] = mapped_column(String(16))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_downloaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ExportTask(Base, TimestampMixin):
    """异步导出任务（文档 A-32）。"""

    __tablename__ = "export_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id", ondelete="SET NULL"))
    export_type: Mapped[str] = mapped_column(String(30), default="registrations", nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    file_path: Mapped[str | None] = mapped_column(String(500))
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ExportTaskStatus.PENDING, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime)
