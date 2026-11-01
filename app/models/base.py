"""模型公共定义：主键/引用类型、时间戳与软删除混入。

类型映射遵循《数据库设计说明书 V1.0》第 2 章：
- 主键与外键引用统一 BIGINT UNSIGNED（SQLite 下降级为 INTEGER 以保留自增能力）；
- 人数、年级、排序等使用无符号整型；
- 学时 DECIMAL(4,1)；
- 时间统一 DATETIME（存东八区本地时间，不用 TIMESTAMP）；
- 不使用数据库外键约束，关联完整性由应用层与事务保证（文档 2.2）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, func
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.timeutil import now_local
from app.database import Base

__all__ = [
    "Base",
    "BigId",
    "RefId",
    "IntU",
    "SmallIntU",
    "TinyIntU",
    "TimestampMixin",
    "SoftDeleteMixin",
    "now_local",
]

# --- 主键 / 外键引用：BIGINT UNSIGNED ---
_BIGINT_U = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")
BigId = _BIGINT_U.with_variant(Integer, "sqlite")
RefId = _BIGINT_U.with_variant(Integer, "sqlite")

# --- 无符号整型 ---
IntU = Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")
SmallIntU = SmallInteger().with_variant(mysql.SMALLINT(unsigned=True), "mysql")
TinyIntU = SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")


class TimestampMixin:
    """通用字段：created_at / updated_at（文档 2.3）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_local, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_local, onupdate=now_local, server_default=func.now()
    )


class SoftDeleteMixin:
    """软删除标记：deleted_at 非空表示已删除（文档 2.5）。"""

    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, at: datetime | None = None) -> None:
        self.deleted_at = at or now_local()
