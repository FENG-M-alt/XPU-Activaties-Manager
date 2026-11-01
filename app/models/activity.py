"""activities 活动表（文档 4.3）。

说明（文档 4.3 末段）：
- 签到窗口由 start_time - checkin_before 与 end_time + checkin_after 实时计算，不落库；
- 报名人数不设冗余列，按 registrations 聚合；
- 软删除使用 deleted_at；
- type_id 关联 dict_items.id（不做数据库外键）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, Index, Numeric, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ActivityStatus
from app.models.base import (
    Base,
    BigId,
    IntU,
    RefId,
    SmallIntU,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.models.dict_item import DictItem

# 活动简介使用 MEDIUMTEXT（文档 2.8），其他方言退化为 TEXT
_MEDIUMTEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class Activity(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    type_id: Mapped[int] = mapped_column(RefId, nullable=False)  # 字典项 ID
    host: Mapped[str] = mapped_column(String(50), nullable=False)
    organizer: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    cover_url: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(_MEDIUMTEXT, nullable=False, default="")
    notice: Mapped[str | None] = mapped_column(Text)

    contact_name: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False, default="")

    signup_start: Mapped[datetime] = mapped_column(nullable=False)
    signup_end: Mapped[datetime] = mapped_column(nullable=False)
    start_time: Mapped[datetime] = mapped_column(nullable=False)
    end_time: Mapped[datetime] = mapped_column(nullable=False)

    capacity: Mapped[int] = mapped_column(IntU, nullable=False, default=0)
    need_audit: Mapped[bool] = mapped_column(nullable=False, default=False)
    allow_cancel: Mapped[bool] = mapped_column(nullable=False, default=True)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False, default=Decimal("1.0"))
    checkin_before: Mapped[int] = mapped_column(SmallIntU, nullable=False, default=30)
    checkin_after: Mapped[int] = mapped_column(SmallIntU, nullable=False, default=30)

    checkin_code: Mapped[str | None] = mapped_column(CHAR(6))
    checkin_code_generated_at: Mapped[datetime | None] = mapped_column()
    checkin_code_reset_at: Mapped[datetime | None] = mapped_column()
    share_token: Mapped[str | None] = mapped_column(CHAR(12))
    share_expire_at: Mapped[datetime | None] = mapped_column()

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ActivityStatus.DRAFT)
    version: Mapped[int] = mapped_column(IntU, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(RefId, nullable=False)

    __table_args__ = (
        Index("uk_checkin_code", "checkin_code", unique=True),
        Index("uk_share_token", "share_token", unique=True),
        Index("idx_status_start", "status", "start_time"),
        Index("idx_type", "type_id"),
        Index("idx_created_by", "created_by"),
    )

    # 活动类型（dict_items，无数据库外键，用 primaryjoin 显式声明）
    activity_type: Mapped[DictItem | None] = relationship(
        primaryjoin="foreign(Activity.type_id) == DictItem.id",
        lazy="joined",
        viewonly=True,
    )
