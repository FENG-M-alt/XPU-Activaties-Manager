"""settings 系统参数表（文档 4.8）。

key 为 MySQL 保留字，SQL 中必须使用反引号 —— SQLAlchemy 会自动引用该标识符。
参数值统一存字符串，按类型解析。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, RefId, now_local


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    remark: Mapped[str | None] = mapped_column(String(200))
    updated_by: Mapped[int | None] = mapped_column(RefId)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=now_local, onupdate=now_local
    )
