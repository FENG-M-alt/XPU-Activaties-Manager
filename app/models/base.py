"""模型公共基类与时间戳字段。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import now_local
from app.database import Base

__all__ = ["Base", "TimestampMixin", "now_local"]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local, nullable=False)
