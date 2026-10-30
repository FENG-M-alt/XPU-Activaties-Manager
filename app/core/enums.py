"""枚举取值（文档 6.1）。"""
from __future__ import annotations

from enum import StrEnum


class ActivityStatus(StrEnum):
    DRAFT = "DRAFT"              # 草稿
    PUBLISHED = "PUBLISHED"      # 已发布
    REGISTERING = "REGISTERING"  # 报名中
    ONGOING = "ONGOING"          # 进行中
    FINISHED = "FINISHED"        # 已结束
    CANCELLED = "CANCELLED"      # 已取消


class RegistrationStatus(StrEnum):
    PENDING = "PENDING"          # 待审核
    APPROVED = "APPROVED"        # 已通过
    REJECTED = "REJECTED"        # 已拒绝
    CANCELLED = "CANCELLED"      # 已取消
    CHECKED_IN = "CHECKED_IN"    # 已签到
    COMPLETED = "COMPLETED"      # 已完成
    ABSENT = "ABSENT"            # 缺席


class AuditMode(StrEnum):
    NONE = "NONE"
    MANUAL = "MANUAL"


class CheckinMethod(StrEnum):
    CODE = "CODE"
    MANUAL = "MANUAL"


class CheckinAction(StrEnum):
    CHECKIN = "CHECKIN"
    UNDO = "UNDO"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AdminRole(StrEnum):
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class DataScope(StrEnum):
    ALL = "ALL"
    COLLEGE = "COLLEGE"
    SELF = "SELF"


class SignupSource(StrEnum):
    SELF = "SELF"
    ADMIN = "ADMIN"


class ImportResult(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class SignupState(StrEnum):
    """活动详情的 signup_state（文档 4.4）。"""

    NOT_STARTED = "NOT_STARTED"
    OPEN = "OPEN"
    FULL = "FULL"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHECKED_IN = "CHECKED_IN"
    CLOSED = "CLOSED"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class ExportTaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


# 占用名额的报名状态（文档 7.1.1）
OCCUPYING_STATUSES: tuple[str, ...] = (
    RegistrationStatus.PENDING,
    RegistrationStatus.APPROVED,
    RegistrationStatus.CHECKED_IN,
    RegistrationStatus.COMPLETED,
    RegistrationStatus.ABSENT,
)

# 计入统计的报名状态（文档 4.10 口径）
COUNTED_STATUSES: tuple[str, ...] = (
    RegistrationStatus.CHECKED_IN,
    RegistrationStatus.COMPLETED,
)

TERMINAL_REGISTRATION_STATUSES: tuple[str, ...] = (
    RegistrationStatus.REJECTED,
    RegistrationStatus.CANCELLED,
    RegistrationStatus.ABSENT,
)

# 学生端"我的报名"的 FINISHED 聚合（文档 4.8）
FINISHED_GROUP_STATUSES: tuple[str, ...] = (
    RegistrationStatus.CHECKED_IN,
    RegistrationStatus.COMPLETED,
    RegistrationStatus.ABSENT,
)
