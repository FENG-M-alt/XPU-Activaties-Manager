"""签到业务：学生签到码签到、管理员补签与撤销。

对齐《数据库设计说明书 V1.0》：
- registrations 只保留 checkin_at / checkin_method / is_late，操作人、原因、IP 写入 checkin_logs（4.5）；
- 撤销签到追加一条 UNDO 流水，同时回退 registrations 的签到字段，两处保留痕迹（4.5 说明）；
- 无 hours_awarded 列，学时由 activities.hours 结合状态统计得出；
- 连续输错计数属瞬时状态，用进程内 TTL 缓存（不落库）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import cache
from app.core.enums import (
    ActivityStatus,
    CheckinAction,
    CheckinMethod,
    RegistrationStatus,
)
from app.core.errors import ErrorCode, fail
from app.core.security import constant_time_equals
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity
from app.models.checkin_log import CheckinLog
from app.models.registration import Registration
from app.models.setting import Setting
from app.models.user import User
from app.services.activity import checkin_window, is_expired_code
from app.services.registration import compute_is_late


# ---------------------------------------------------------------- 参数
def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    """读取 settings 表参数，缺失时返回默认值。"""
    row = db.get(Setting, key)
    if row is None or row.value is None:
        return default
    return row.value


def _record_ip_enabled(db: Session) -> bool:
    return str(get_setting(db, "record_checkin_ip", "false")).strip().lower() in ("1", "true", "yes")


def _makeup_days(db: Session) -> int:
    raw = get_setting(db, "checkin_makeup_days", str(settings.MAKEUP_CHECKIN_DAYS))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return settings.MAKEUP_CHECKIN_DAYS


# ---------------------------------------------------------------- 连错锁定（内存）
def _lock_key(activity_id: int, user_id: int) -> str:
    return f"checkin_fail:{activity_id}:{user_id}"


def recent_failures(activity_id: int, user_id: int) -> int:
    return int(cache.get(_lock_key(activity_id, user_id), 0) or 0)


def is_locked(activity_id: int, user_id: int) -> bool:
    return recent_failures(activity_id, user_id) >= settings.CHECKIN_MAX_FAILURES


def _record_failure(activity_id: int, user_id: int) -> int:
    return cache.incr(_lock_key(activity_id, user_id), settings.CHECKIN_LOCK_MINUTES * 60)


def _clear_failures(activity_id: int, user_id: int) -> None:
    cache.delete(_lock_key(activity_id, user_id))


# ---------------------------------------------------------------- 学生签到
def submit_checkin(
    db: Session,
    activity: Activity,
    user: User,
    code: str,
    *,
    ip: str | None = None,
    now: datetime | None = None,
) -> dict:
    """按接口文档 4.9 的校验顺序执行签到，返回响应 data。"""
    now = now or now_local()

    # 2. 活动未被取消、未结束
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消，签到已失效")
    if activity.status == ActivityStatus.FINISHED or (activity.end_time and now > activity.end_time):
        fail(ErrorCode.ILLEGAL_STATE, "活动已结束，无法签到")

    # 3. 存在报名记录
    registration = db.scalars(
        select(Registration).where(
            Registration.activity_id == activity.id,
            Registration.user_id == user.id,
            Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
        )
    ).first()
    if registration is None:
        fail(ErrorCode.NOT_FOUND, "未找到报名记录，请先报名")

    # 4. 报名状态（已签到单独识别，避免落入"未通过审核"）
    if registration.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(ErrorCode.ALREADY_CHECKED_IN, f"已签到，签到时间 {to_iso(registration.checkin_at)}")
    if registration.status != RegistrationStatus.APPROVED:
        fail(ErrorCode.NOT_APPROVED)

    # 5. 签到时间窗
    window_start, window_end = checkin_window(activity)
    if window_start and now < window_start:
        fail(ErrorCode.CHECKIN_WINDOW_CLOSED, f"签到尚未开始，可签到时间 {to_iso(window_start)}")
    if window_end and now > window_end:
        fail(ErrorCode.CHECKIN_WINDOW_CLOSED, f"签到已结束，签到截止 {to_iso(window_end)}")

    # 8. 尝试次数（锁定期间直接拒绝）
    if is_locked(activity.id, user.id):
        fail(ErrorCode.CHECKIN_LOCKED)

    # 6. 签到码校验（恒定时间比较）
    if not activity.checkin_code:
        fail(ErrorCode.CHECKIN_CODE_EXPIRED)
    if not constant_time_equals(code, activity.checkin_code):
        if is_expired_code(activity.id, code):
            fail(ErrorCode.CHECKIN_CODE_EXPIRED)
        failures = _record_failure(activity.id, user.id)
        if failures >= settings.CHECKIN_MAX_FAILURES:
            fail(ErrorCode.CHECKIN_LOCKED)
        fail(ErrorCode.CHECKIN_CODE_WRONG)

    # 7. 重复签到
    if registration.checkin_at is not None:
        fail(ErrorCode.ALREADY_CHECKED_IN, f"已签到，签到时间 {to_iso(registration.checkin_at)}")

    # 通过：更新报名签到字段 + 追加签到流水
    grace_raw = get_setting(db, "checkin_late_grace_minutes", "0") or "0"
    try:
        grace = int(grace_raw)
    except ValueError:
        grace = 0

    registration.status = RegistrationStatus.CHECKED_IN
    registration.checkin_at = now
    registration.checkin_method = CheckinMethod.CODE
    registration.is_late = compute_is_late(activity, now, grace)
    db.add(registration)

    db.add(
        CheckinLog(
            registration_id=registration.id,
            action=CheckinAction.CHECKIN,
            method=CheckinMethod.CODE,
            operator_id=None,  # 学生自助签到，无操作人
            ip=ip if _record_ip_enabled(db) else None,
            created_at=now,
        )
    )
    _clear_failures(activity.id, user.id)
    db.commit()
    db.refresh(registration)

    return {
        "checkin_at": to_iso(registration.checkin_at),
        "is_late": registration.is_late,
        "hours": float(activity.hours or 0),
        "status": registration.status,
        "checkin_method": registration.checkin_method,
    }


# ---------------------------------------------------------------- 管理员补签
def manual_checkin(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    admin_name: str | None = None,
    reason: str,
    checkin_at: datetime | None = None,
    is_super_admin: bool = False,
    ip: str | None = None,
    now: datetime | None = None,
) -> Registration:
    """A-18 补签：registrations 记为 MANUAL，原因与操作人写入 checkin_logs。"""
    now = now or now_local()
    activity = registration.activity
    checkin_at = checkin_at or now

    if registration.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(ErrorCode.ALREADY_CHECKED_IN, "该学生已签到")
    if registration.status in (RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED):
        fail(ErrorCode.ILLEGAL_STATE, "该报名的状态不允许补签")
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消，无法补签")

    if activity.start_time and checkin_at < activity.start_time - timedelta(minutes=activity.checkin_before or 0):
        fail(
            ErrorCode.PARAM_INVALID,
            "补签时间早于活动开始时间",
            data={"field": "checkin_at", "reason": "早于活动开始时间"},
        )
    if activity.end_time and checkin_at > activity.end_time:
        fail(
            ErrorCode.PARAM_INVALID,
            "补签时间晚于活动结束时间",
            data={"field": "checkin_at", "reason": "晚于活动结束时间"},
        )

    days = _makeup_days(db)
    if activity.end_time and now > activity.end_time + timedelta(days=days) and not is_super_admin:
        fail(ErrorCode.FORBIDDEN, f"已超过补签期限（活动结束后 {days} 天），请联系超级管理员")

    registration.status = RegistrationStatus.CHECKED_IN
    registration.checkin_at = checkin_at
    registration.checkin_method = CheckinMethod.MANUAL
    registration.is_late = compute_is_late(activity, checkin_at)
    db.add(registration)

    db.add(
        CheckinLog(
            registration_id=registration.id,
            action=CheckinAction.CHECKIN,
            method=CheckinMethod.MANUAL,
            operator_id=admin_id,
            reason=reason,
            ip=ip if _record_ip_enabled(db) else None,
            created_at=now,
        )
    )
    return registration


def undo_checkin(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    reason: str,
    ip: str | None = None,
    now: datetime | None = None,
) -> Registration:
    """A-19 撤销签到（仅超管）：回退报名字段并追加 UNDO 流水，学时随状态自动扣减。"""
    now = now or now_local()
    if registration.status not in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(ErrorCode.ILLEGAL_STATE, "该报名未签到，无需撤销")

    method = registration.checkin_method or CheckinMethod.MANUAL

    registration.status = RegistrationStatus.APPROVED
    registration.checkin_at = None
    registration.checkin_method = None
    registration.is_late = False
    db.add(registration)

    db.add(
        CheckinLog(
            registration_id=registration.id,
            action=CheckinAction.UNDO,
            method=method,
            operator_id=admin_id,
            reason=reason,
            ip=ip,
            created_at=now,
        )
    )
    return registration


def checkin_logs_of(db: Session, registration_id: int) -> list[CheckinLog]:
    """按报名查签到流水（用于报名详情追溯）。"""
    return list(
        db.scalars(
            select(CheckinLog)
            .where(CheckinLog.registration_id == registration_id)
            .order_by(CheckinLog.created_at.asc())
        ).all()
    )
