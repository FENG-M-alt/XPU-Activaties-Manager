"""dict_items 字典项表（文档 4.9）。

活动类型、拒绝理由、公告分类统一由本表承载，activities.type_id 关联本表 id。
"""
from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, SmallIntU, TimestampMixin


class DictItem(Base, TimestampMixin):
    __tablename__ = "dict_items"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    dict_type: Mapped[str] = mapped_column(String(32), nullable=False)  # activity_type / reject_reason / notice_type
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[str] = mapped_column(String(32), nullable=False)
    sort: Mapped[int] = mapped_column(SmallIntU, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    __table_args__ = (
        Index("uk_type_value", "dict_type", "value", unique=True),
        Index("idx_type_sort", "dict_type", "sort"),
    )


class DictType:
    """字典类型常量。"""

    ACTIVITY_TYPE = "activity_type"
    REJECT_REASON = "reject_reason"
    NOTICE_TYPE = "notice_type"
