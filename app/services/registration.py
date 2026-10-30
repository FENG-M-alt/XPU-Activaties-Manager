"""报名业务：名额占用、创建、取消、审核（文档 4.6 / 4.7 / 7.1 / 7.2）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    OCCUPYING_STATUSES,
    ActivityStatus,
    RegistrationStatus,
    SignupSource,
)
from app.core.errors import ErrorCode, fail
from app.core.timeutil import now_local
from app.models.activity import Activity
from app.models.registration import Registration
from app.models.student import Student
from app.services.activity import checkin_window, is_signup_open


def count_occupying(db: Session, activity_id: int) -> int:
    """有效报名数：待审核、已通过、已签到、已完成、缺席（文档 7.1.1）。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(
                Registration.activity_id == activity_id,
                Registration.status.in_(OCCUPYING_STATUSES),
            )
        )
        or 0
    )


def recount_registered(db: Session, activity: Activity) -> int:
    """重算并写回活动已报名数，修正计数漂移。"""
    count = count_occupying(db, activity.id)
    activity.registered_count = count
    return count


def find_active_registration(db: Session, activity_id: int, student_id: int) -> Registration | None:
    return db.scalars(
        select(Registration).where(
            Registration.activity_id == activity_id,
            Registration.student_id == student_id,
            Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
        )
    ).first()


def occupy_slot(db: Session, activity: Activity) -> None:
    """原子占用名额，防止超卖（文档 2.5.4）。"""
    result = db.execute(
        update(Activity)
        .where(
            Activity.id == activity.id,
            Activity.registered_count < Activity.capacity,
        )
        .values(registered_count=Activity.registered_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        db.rollback()
        fail(ErrorCode.CAPACITY_FULL)
    db.refresh(activity)


def release_slot(db: Session, activity: Activity) -> None:
    db.execute(
        update(Activity)
        .where(Activity.id == activity.id, Activity.registered_count > 0)
        .values(registered_count=Activity.registered_count - 1)
        .execution_options(synchronize_session=False)
    )
    db.refresh(activity)


def create_registration(
    db: Session,
    activity: Activity,
    student: Student,
    *,
    source: str = SignupSource.SELF,
    now: datetime | None = None,
) -> Registration:
    """提交报名：调用方须已完成文档 4.6 的校验顺序 1—5。"""
    now = now or now_local()
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消")
    if not is_signup_open(activity, now):
        fail(ErrorCode.SIGNUP_WINDOW_CLOSED)
    if find_active_registration(db, activity.id, student.id):
        fail(ErrorCode.DUPLICATE_SIGNUP)

    occupy_slot(db, activity)

    status = RegistrationStatus.PENDING if activity.need_audit else RegistrationStatus.APPROVED
    registration = Registration(
        activity_id=activity.id,
        student_id=student.id,
        status=status,
        signup_at=now,
        source=source,
        hours_awarded=0,
    )
    db.add(registration)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        fail(ErrorCode.DUPLICATE_SIGNUP)
    return registration


def cancel_registration(
    db: Session,
    registration: Registration,
    *,
    reason: str | None = None,
    by_admin: bool = False,
    now: datetime | None = None,
) -> Registration:
    """取消报名，立即释放名额并保留记录用于追溯（文档 4.7）。"""
    now = now or now_local()
    activity = registration.activity

    if registration.status in (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED):
        fail(ErrorCode.ILLEGAL_STATE, "已签到的报名不可取消，请联系管理员处理")
    if registration.status in (RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED):
        fail(ErrorCode.ILLEGAL_STATE, "该报名已终止，无需取消")
    if not by_admin:
        if not activity.allow_cancel:
            fail(ErrorCode.ILLEGAL_STATE, "该活动不允许取消报名")
        if activity.status == ActivityStatus.CANCELLED:
            fail(ErrorCode.ILLEGAL_STATE, "活动已取消")
        # 报名截止前可取消；活动开始后不允许（文档 4.7.1）
        if activity.signup_end and now > activity.signup_end:
            fail(ErrorCode.ILLEGAL_STATE, "报名已截止，无法取消")
        if activity.start_time and now >= activity.start_time:
            fail(ErrorCode.ILLEGAL_STATE, "活动已开始，无法取消报名")

    registration.status = RegistrationStatus.CANCELLED
    registration.cancel_reason = reason or "学生主动取消"
    registration.cancelled_at = now
    db.add(registration)
    db.flush()
    release_slot(db, activity)
    return registration


def approve_registration(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    remark: str | None = None,
    now: datetime | None = None,
) -> Registration:
    now = now or now_local()
    if registration.status != RegistrationStatus.PENDING:
        fail(ErrorCode.ILLEGAL_STATE, "仅待审核的报名可以审核通过")
    activity = registration.activity
    if activity.registered_count > activity.capacity:
        fail(ErrorCode.CAPACITY_FULL)
    registration.status = RegistrationStatus.APPROVED
    registration.audit_by = admin_id
    registration.audit_at = now
    registration.audit_remark = remark or "审核通过"
    db.add(registration)
    return registration


def reject_registration(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    reason: str,
    now: datetime | None = None,
) -> Registration:
    now = now or now_local()
    if registration.status != RegistrationStatus.PENDING:
        fail(ErrorCode.ILLEGAL_STATE, "仅待审核的报名可以拒绝")
    registration.status = RegistrationStatus.REJECTED
    registration.audit_by = admin_id
    registration.audit_at = now
    registration.audit_remark = reason
    db.add(registration)
    db.flush()
    release_slot(db, registration.activity)
    return registration


def checkin_window_info(activity: Activity) -> dict:
    start, end = checkin_window(activity)
    from app.core.timeutil import to_iso

    return {"start": to_iso(start), "end": to_iso(end)}


def compute_is_late(activity: Activity, checkin_at: datetime, grace_minutes: int = 0) -> bool:
    """是否晚于活动开始时间（文档 4.9）。"""
    from datetime import timedelta

    if not activity.start_time:
        return False
    return checkin_at > activity.start_time + timedelta(minutes=grace_minutes)
