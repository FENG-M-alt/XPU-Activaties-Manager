"""签到业务：学生签到码签到、管理员补签与撤销（文档 4.9 / 5.8 / 7.3）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import (
    ActivityStatus,
    CheckinMethod,
    RegistrationStatus,
)
from app.core.errors import ErrorCode, fail
from app.core.security import constant_time_equals
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity
from app.models.audit import CheckinAttempt
from app.models.registration import Registration
from app.models.student import Student
from app.services.activity import checkin_window, is_expired_code
from app.services.registration import compute_is_late


# ---------------------------------------------------------------- 锁定
def recent_failures(db: Session, activity_id: int, student_id: int, now: datetime) -> int:
    since = now - timedelta(minutes=settings.CHECKIN_LOCK_MINUTES)
    return int(
        db.scalar(
            select(func.count())
            .select_from(CheckinAttempt)
            .where(
                CheckinAttempt.activity_id == activity_id,
                CheckinAttempt.student_id == student_id,
                CheckinAttempt.success.is_(False),
                CheckinAttempt.created_at >= since,
            )
        )
        or 0
    )


def is_locked(db: Session, activity_id: int, student_id: int, now: datetime) -> bool:
    return recent_failures(db, activity_id, student_id, now) >= settings.CHECKIN_MAX_FAILURES


def _record_failure(db: Session, activity_id: int, student_id: int, ip: str | None, now: datetime) -> int:
    db.add(
        CheckinAttempt(
            activity_id=activity_id,
            student_id=student_id,
            success=False,
            ip=ip,
            created_at=now,
        )
    )
    db.commit()
    return recent_failures(db, activity_id, student_id, now)


# ---------------------------------------------------------------- 学生签到
def submit_checkin(
    db: Session,
    activity: Activity,
    student: Student,
    code: str,
    *,
    ip: str | None = None,
    now: datetime | None = None,
) -> dict:
    """按文档 4.9 校验顺序执行签到，返回响应 data。"""
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
            Registration.student_id == student.id,
            Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
        )
    ).first()
    if registration is None:
        fail(ErrorCode.NOT_FOUND, "未找到报名记录，请先报名")

    # 4. 报名状态（已签到单独识别，避免落入"未通过审核"）
    if registration.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(
            ErrorCode.ALREADY_CHECKED_IN,
            f"已签到，签到时间 {to_iso(registration.checkin_at)}",
        )
    if registration.status not in (RegistrationStatus.APPROVED,):
        fail(ErrorCode.NOT_APPROVED)

    # 5. 签到时间窗
    window_start, window_end = checkin_window(activity)
    if window_start and now < window_start:
        fail(ErrorCode.CHECKIN_WINDOW_CLOSED, f"签到尚未开始，可签到时间 {to_iso(window_start)}")
    if window_end and now > window_end:
        fail(ErrorCode.CHECKIN_WINDOW_CLOSED, f"签到已结束，签到截止 {to_iso(window_end)}")

    # 8. 尝试次数（锁定期间直接拒绝）
    if is_locked(db, activity.id, student.id, now):
        fail(ErrorCode.CHECKIN_LOCKED)

    # 6. 签到码校验（恒定时间比较）
    if not activity.checkin_code:
        fail(ErrorCode.CHECKIN_CODE_EXPIRED)
    if not constant_time_equals(code, activity.checkin_code):
        if is_expired_code(db, activity.id, code):
            fail(ErrorCode.CHECKIN_CODE_EXPIRED)
        failures = _record_failure(db, activity.id, student.id, ip, now)
        if failures >= settings.CHECKIN_MAX_FAILURES:
            fail(ErrorCode.CHECKIN_LOCKED)
        fail(ErrorCode.CHECKIN_CODE_WRONG)

    # 7. 重复签到
    if registration.checkin_at is not None:
        fail(ErrorCode.ALREADY_CHECKED_IN, f"已签到，签到时间 {to_iso(registration.checkin_at)}")

    # --- 通过：写入签到信息
    grace = int(get_setting(db, "checkin_late_grace_minutes", "0") or 0)
    registration.status = RegistrationStatus.CHECKED_IN
    registration.checkin_at = now
    registration.checkin_method = CheckinMethod.CODE
    registration.is_late = compute_is_late(activity, now, grace)
    registration.hours_awarded = Decimal(str(activity.hours or 0))
    if get_setting(db, "checkin_record_ip", "true").lower() in ("1", "true", "yes"):
        registration.checkin_ip = ip
    db.add(registration)
    db.add(CheckinAttempt(activity_id=activity.id, student_id=student.id, success=True, ip=ip, created_at=now))
    _apply_student_totals(db, student.id)
    db.commit()
    db.refresh(registration)

    return {
        "checkin_at": to_iso(registration.checkin_at),
        "is_late": registration.is_late,
        "hours": float(registration.hours_awarded or 0),
        "status": registration.status,
    }


# ---------------------------------------------------------------- 管理员补签
def manual_checkin(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    admin_name: str | None,
    reason: str,
    checkin_at: datetime | None = None,
    is_super_admin: bool = False,
    ip: str | None = None,
    now: datetime | None = None,
) -> Registration:
    """A-18 补签：签到方式 MANUAL，记录操作人与原因。"""
    now = now or now_local()
    activity = registration.activity
    checkin_at = checkin_at or now

    if registration.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(ErrorCode.ALREADY_CHECKED_IN, "该学生已签到")
    if registration.status in (RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED):
        fail(ErrorCode.ILLEGAL_STATE, "该报名的状态不允许补签")
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消，无法补签")

    # 补签时间须在活动时间范围内
    if activity.start_time and checkin_at < activity.start_time - timedelta(minutes=activity.checkin_before or 0):
        fail(ErrorCode.PARAM_INVALID, "补签时间早于活动开始时间", data={"field": "checkin_at", "reason": "早于活动开始时间"})
    if activity.end_time and checkin_at > activity.end_time:
        fail(ErrorCode.PARAM_INVALID, "补签时间晚于活动结束时间", data={"field": "checkin_at", "reason": "晚于活动结束时间"})

    # 补签期限：活动结束后 N 天，超期需超管（文档 5.8）
    if activity.end_time and now > activity.end_time + timedelta(days=settings.MAKEUP_CHECKIN_DAYS) and not is_super_admin:
        fail(ErrorCode.FORBIDDEN, f"已超过补签期限（活动结束后 {settings.MAKEUP_CHECKIN_DAYS} 天），请联系超级管理员")

    registration.status = RegistrationStatus.CHECKED_IN
    registration.checkin_at = checkin_at
    registration.checkin_method = CheckinMethod.MANUAL
    registration.checkin_by = admin_id
    registration.checkin_reason = reason
    registration.checkin_ip = ip
    registration.is_late = compute_is_late(activity, checkin_at)
    registration.hours_awarded = Decimal(str(activity.hours or 0))
    db.add(registration)
    _apply_student_totals(db, registration.student_id)
    return registration


def undo_checkin(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    reason: str,
    now: datetime | None = None,
) -> Registration:
    """A-19 撤销签到（仅超管）：报名回到 APPROVED 并扣减学时。"""
    now = now or now_local()
    if registration.status not in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(ErrorCode.ILLEGAL_STATE, "该报名未签到，无需撤销")
    registration.status = RegistrationStatus.APPROVED
    registration.checkin_at = None
    registration.checkin_method = None
    registration.checkin_by = admin_id
    registration.checkin_reason = f"撤销签到：{reason}"
    registration.is_late = False
    registration.hours_awarded = Decimal("0.0")
    db.add(registration)
    _apply_student_totals(db, registration.student_id)
    return registration


def _apply_student_totals(db: Session, student_id: int) -> None:
    """刷新学生累计学时与活动数（去重统计已签到/已完成）。"""
    from app.core.enums import COUNTED_STATUSES

    row = db.execute(
        select(func.count(func.distinct(Registration.activity_id)), func.coalesce(func.sum(Registration.hours_awarded), 0))
        .where(Registration.student_id == student_id, Registration.status.in_(COUNTED_STATUSES))
    ).one()
    db.execute(
        update(Student)
        .where(Student.id == student_id)
        .values(total_activities=int(row[0] or 0), total_hours=float(row[1] or 0))
        .execution_options(synchronize_session=False)
    )


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    """读取系统参数（带默认值回退）。"""
    from app.models.system import SystemSetting

    row = db.get(SystemSetting, key)
    if row is None:
        return default
    return row.value if row.value is not None else (row.default_value if row.default_value is not None else default)
