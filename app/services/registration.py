"""报名业务：名额校验、创建/复用、取消、审核。

对齐《数据库设计说明书 V1.0》：
- 不设报名人数冗余列，有效报名数按状态聚合（4.3 末段、6.1.2）；
- 取消报名复用原行，再次报名重置该行（4.4 约束、6.1.4），因此 uk_activity_user 始终有效；
- 名额校验与插入在同一事务内：先 SELECT ... FOR UPDATE 锁活动行，再统计，再写（6.2）；
- 报名时写入 college/major/class 快照（6.5）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
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
from app.models.user import User
from app.services.activity import checkin_window, is_signup_open

# 审核通过时的"已占名额"状态（不含 PENDING，因为 PENDING 尚未通过审核）
APPROVED_STATUSES: tuple[str, ...] = (
    RegistrationStatus.APPROVED,
    RegistrationStatus.CHECKED_IN,
    RegistrationStatus.COMPLETED,
    RegistrationStatus.ABSENT,
)


def count_occupying(db: Session, activity_id: int) -> int:
    """有效报名数：待审核、已通过、已签到、已完成、缺席（接口文档 7.1.1）。"""
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


def count_occupying_locked(db: Session, activity_id: int) -> int:
    """加锁计数（当前读）。

    必须在已持有活动行锁之后调用：MySQL 默认 REPEATABLE READ 下普通 SELECT 走事务快照，
    看不到其它事务刚提交的报名，会放过超员请求；`FOR UPDATE` 属"当前读"，
    返回已提交的最新数据。SQLite 忽略 FOR UPDATE，但写事务本身串行。
    """
    stmt = (
        select(Registration.id)
        .where(
            Registration.activity_id == activity_id,
            Registration.status.in_(OCCUPYING_STATUSES),
        )
        .with_for_update()
    )
    return len(db.scalars(stmt).all())


def count_approved(db: Session, activity_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(
                Registration.activity_id == activity_id,
                Registration.status.in_(APPROVED_STATUSES),
            )
        )
        or 0
    )


def find_active_registration(db: Session, activity_id: int, user_id: int) -> Registration | None:
    return db.scalars(
        select(Registration).where(
            Registration.activity_id == activity_id,
            Registration.user_id == user_id,
            Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
        )
    ).first()


def find_any_registration(db: Session, activity_id: int, user_id: int) -> Registration | None:
    """任意状态的报名行（含已取消/已拒绝），用于复用。"""
    return db.scalars(
        select(Registration).where(
            Registration.activity_id == activity_id,
            Registration.user_id == user_id,
        )
    ).first()


def lock_activity(db: Session, activity_id: int) -> Activity:
    """对活动行加写锁（MySQL: SELECT ... FOR UPDATE；SQLite 由写事务串行化）。"""
    row = db.execute(
        select(Activity).where(Activity.id == activity_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        fail(ErrorCode.NOT_FOUND, "活动不存在")
    return row


def _apply_snapshots(registration: Registration, user: User) -> None:
    registration.college_snapshot = user.college or ""
    registration.major_snapshot = user.major
    registration.class_snapshot = user.class_name


def create_registration(
    db: Session,
    activity: Activity,
    user: User,
    *,
    source: str = SignupSource.SELF,
    now: datetime | None = None,
) -> Registration:
    """提交报名：调用方须已完成接口文档 4.6 的校验 1—3，这里完成 4—5 并落库。"""
    now = now or now_local()
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消")
    if not is_signup_open(activity, now):
        fail(ErrorCode.SIGNUP_WINDOW_CLOSED)

    # 锁活动行，保证"统计 + 写入"原子（数据库文档 6.2）
    locked = lock_activity(db, activity.id)

    existing = find_any_registration(db, activity.id, user.id)
    if existing is not None and existing.status not in (
        RegistrationStatus.CANCELLED,
        RegistrationStatus.REJECTED,
    ):
        fail(ErrorCode.DUPLICATE_SIGNUP)

    # 用加锁计数（当前读），避免 REPEATABLE READ 快照导致超卖
    used = count_occupying_locked(db, activity.id)
    if used >= locked.capacity:
        fail(ErrorCode.CAPACITY_FULL)

    status = RegistrationStatus.PENDING if locked.need_audit else RegistrationStatus.APPROVED

    if existing is not None:
        # 复用被取消/拒绝的行（数据库文档 4.4 约束、6.1.4）
        registration = existing
        registration.status = status
        registration.signup_at = now
        registration.source = source
        registration.cancel_reason = None
        registration.audit_by = None
        registration.audit_at = None
        registration.audit_remark = None
        registration.checkin_at = None
        registration.checkin_method = None
        registration.is_late = False
        _apply_snapshots(registration, user)
    else:
        registration = Registration(
            activity_id=locked.id,
            user_id=user.id,
            status=status,
            signup_at=now,
            source=source,
            is_late=False,
        )
        _apply_snapshots(registration, user)
        db.add(registration)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        fail(ErrorCode.DUPLICATE_SIGNUP)

    # 让内存中的 activity 与库内一致
    activity.status = locked.status
    return registration


def cancel_registration(
    db: Session,
    registration: Registration,
    *,
    reason: str | None = None,
    by_admin: bool = False,
    now: datetime | None = None,
) -> Registration:
    """取消报名：置 CANCELLED 并记录原因；名额随状态自动释放（数据库文档 6.1.4）。"""
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
        # 报名截止前可取消；活动开始后不允许（接口文档 4.7.1）
        if activity.signup_end and now > activity.signup_end:
            fail(ErrorCode.ILLEGAL_STATE, "报名已截止，无法取消")
        if activity.start_time and now >= activity.start_time:
            fail(ErrorCode.ILLEGAL_STATE, "活动已开始，无法取消报名")

    registration.status = RegistrationStatus.CANCELLED
    registration.cancel_reason = reason or "学生主动取消"
    db.add(registration)
    db.flush()
    return registration


def approve_registration(
    db: Session,
    registration: Registration,
    *,
    admin_id: int,
    remark: str | None = None,
    now: datetime | None = None,
) -> Registration:
    """审核通过：通过数量不得超过名额上限（接口文档 7.2.2）。"""
    now = now or now_local()
    if registration.status != RegistrationStatus.PENDING:
        fail(ErrorCode.ILLEGAL_STATE, "仅待审核的报名可以审核通过")

    activity = lock_activity(db, registration.activity_id)
    if count_approved(db, activity.id) >= activity.capacity:
        fail(ErrorCode.CAPACITY_FULL, "名额不足，无法通过审核")

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
    return registration


def checkin_window_info(activity: Activity) -> dict:
    from app.core.timeutil import to_iso

    start, end = checkin_window(activity)
    return {"start": to_iso(start), "end": to_iso(end)}


def compute_is_late(activity: Activity, checkin_at: datetime, grace_minutes: int = 0) -> bool:
    """是否晚于活动开始时间（接口文档 4.9）。"""
    from datetime import timedelta

    if not activity.start_time:
        return False
    return checkin_at > activity.start_time + timedelta(minutes=grace_minutes)
