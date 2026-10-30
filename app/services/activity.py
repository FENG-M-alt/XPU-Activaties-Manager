"""活动状态机与签到码/分享链接（文档 7.3 / 7.4 / 7.5）。

接口读取时按当前时间惰性校正状态，同时由后台任务每分钟推进一次，
避免任务延迟导致页面与时间不一致（文档 7.4 末段）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.enums import (
    ActivityStatus,
    AuditMode,
    RegistrationStatus,
    SignupState,
)
from app.core.errors import ErrorCode, fail
from app.core.security import random_checkin_code, random_share_token
from app.core.timeutil import now_local
from app.models.activity import Activity, ActivityCheckinCode
from app.models.registration import Registration


# ---------------------------------------------------------------- 时间窗
def checkin_window(activity: Activity) -> tuple[datetime | None, datetime | None]:
    if not activity.start_time or not activity.end_time:
        return None, None
    before = activity.checkin_before or 0
    after = activity.checkin_after or 0
    return (
        activity.start_time - timedelta(minutes=before),
        activity.end_time + timedelta(minutes=after),
    )


def signup_window(activity: Activity) -> tuple[datetime | None, datetime | None]:
    return activity.signup_start, activity.signup_end


def is_signup_open(activity: Activity, now: datetime | None = None) -> bool:
    now = now or now_local()
    if activity.status not in (ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING):
        return False
    if activity.signup_start and now < activity.signup_start:
        return False
    if activity.signup_end and now > activity.signup_end:
        return False
    return True


# ---------------------------------------------------------------- 派生状态
def effective_status(activity: Activity, now: datetime | None = None) -> str:
    """纯函数：按当前时间推算应显示的状态，不写库。"""
    now = now or now_local()
    status = activity.status
    if status in (ActivityStatus.DRAFT, ActivityStatus.CANCELLED):
        return status
    if activity.end_time and now >= activity.end_time:
        return ActivityStatus.FINISHED
    if activity.start_time and now >= activity.start_time:
        return ActivityStatus.ONGOING
    if status == ActivityStatus.PUBLISHED:
        if activity.signup_start and now >= activity.signup_start:
            return ActivityStatus.REGISTERING
        if not activity.signup_start:
            return ActivityStatus.REGISTERING
    return status


def compute_signup_state(
    activity: Activity,
    registration: Registration | None,
    now: datetime | None = None,
) -> str:
    """活动详情的 signup_state（文档 4.4 取值表）。"""
    now = now or now_local()

    if activity.status == ActivityStatus.CANCELLED:
        return SignupState.CANCELLED
    if registration and registration.status in (
        RegistrationStatus.CHECKED_IN,
        RegistrationStatus.COMPLETED,
    ):
        return SignupState.CHECKED_IN

    status = effective_status(activity, now)
    if status == ActivityStatus.FINISHED:
        return SignupState.FINISHED

    if registration:
        if registration.status == RegistrationStatus.PENDING:
            return SignupState.PENDING
        if registration.status == RegistrationStatus.APPROVED:
            return SignupState.APPROVED
        if registration.status == RegistrationStatus.ABSENT:
            return SignupState.FINISHED

    if activity.signup_start and now < activity.signup_start:
        return SignupState.NOT_STARTED
    if activity.signup_end and now > activity.signup_end:
        return SignupState.CLOSED
    if status not in (ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING, ActivityStatus.ONGOING):
        return SignupState.CLOSED
    if activity.remaining <= 0:
        return SignupState.FULL
    return SignupState.OPEN


# ---------------------------------------------------------------- 状态推进
def sync_activity(db: Session, activity: Activity, now: datetime | None = None) -> bool:
    """按时间推进单个活动状态；返回是否有变更（不提交事务）。"""
    now = now or now_local()
    if activity.status in (ActivityStatus.DRAFT, ActivityStatus.CANCELLED):
        return False

    changed = False
    if activity.end_time and now >= activity.end_time and activity.status != ActivityStatus.FINISHED:
        activity.status = ActivityStatus.FINISHED
        activity.finished_at = activity.finished_at or activity.end_time
        _settle_pending(db, activity, now, reason="活动已开始，未完成审核")
        _mark_absent(db, activity, now)
        _recount(db, activity)
        changed = True
    elif activity.start_time and now >= activity.start_time:
        if activity.status in (ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING):
            activity.status = ActivityStatus.ONGOING
            _settle_pending(db, activity, now, reason="活动已开始，未完成审核")
            _recount(db, activity)
            changed = True
    elif activity.status == ActivityStatus.PUBLISHED:
        if not activity.signup_start or now >= activity.signup_start:
            activity.status = ActivityStatus.REGISTERING
            changed = True

    return changed


def sync_activities(db: Session, activities: list[Activity], now: datetime | None = None) -> bool:
    now = now or now_local()
    changed = False
    for activity in activities:
        if sync_activity(db, activity, now):
            changed = True
    return changed


def sync_all_activities(db: Session, now: datetime | None = None) -> int:
    """后台任务入口：推进所有非终态活动。"""
    now = now or now_local()
    rows = db.scalars(
        select(Activity).where(
            Activity.is_deleted.is_(False),
            Activity.status.in_(
                [ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING, ActivityStatus.ONGOING]
            ),
        )
    ).all()
    changed = 0
    for activity in rows:
        if sync_activity(db, activity, now):
            changed += 1
    if changed:
        db.commit()
    return changed


def _settle_pending(db: Session, activity: Activity, now: datetime, reason: str) -> None:
    """活动开始时仍未审核的报名自动置为已拒绝（文档 7.2.5）。"""
    db.execute(
        update(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status == RegistrationStatus.PENDING,
        )
        .values(
            status=RegistrationStatus.REJECTED,
            audit_at=now,
            audit_remark=reason,
        )
    )


def _mark_absent(db: Session, activity: Activity, now: datetime) -> None:
    """活动结束后未签到的已通过报名置为缺席（文档 A-11 / 7.4）。"""
    db.execute(
        update(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status == RegistrationStatus.APPROVED,
        )
        .values(status=RegistrationStatus.ABSENT)
    )


def _recount(db: Session, activity: Activity) -> None:
    from app.services.registration import recount_registered

    recount_registered(db, activity)


# ---------------------------------------------------------------- 签到码 / 分享
def generate_unique_checkin_code(db: Session, exclude_activity_id: int | None = None) -> str:
    """6 位数字、全局唯一（文档 5.5）。"""
    for _ in range(50):
        code = random_checkin_code()
        stmt = select(func.count()).select_from(Activity).where(Activity.checkin_code == code)
        if exclude_activity_id is not None:
            stmt = stmt.where(Activity.id != exclude_activity_id)
        if not db.scalar(stmt):
            return code
    raise RuntimeError("无法生成唯一签到码，请稍后重试")


def generate_unique_share_token(db: Session) -> str:
    for _ in range(50):
        token = random_share_token()
        if not db.scalar(select(func.count()).select_from(Activity).where(Activity.share_token == token)):
            return token
    raise RuntimeError("无法生成唯一分享链接，请稍后重试")


def issue_checkin_code(
    db: Session,
    activity: Activity,
    *,
    admin_id: int | None,
    reason: str | None = None,
    now: datetime | None = None,
) -> str:
    """生成（或重置）签到码，旧码立即失效（文档 A-13）。"""
    now = now or now_local()
    # 旧码标记失效
    db.execute(
        update(ActivityCheckinCode)
        .where(
            ActivityCheckinCode.activity_id == activity.id,
            ActivityCheckinCode.is_current.is_(True),
        )
        .values(is_current=False, expired_at=now)
    )
    code = generate_unique_checkin_code(db, exclude_activity_id=activity.id)
    activity.checkin_code = code
    if activity.checkin_code_generated_at is None:
        activity.checkin_code_generated_at = now
    else:
        activity.checkin_code_reset_at = now
    db.add(
        ActivityCheckinCode(
            activity_id=activity.id,
            code=code,
            generated_at=now,
            is_current=True,
            reset_reason=reason,
            created_by=admin_id,
        )
    )
    db.flush()
    return code


def issue_share_token(db: Session, activity: Activity, now: datetime | None = None) -> str:
    now = now or now_local()
    token = generate_unique_share_token(db)
    activity.share_token = token
    activity.share_expire_at = None  # 默认长期有效，可由管理员设置
    db.flush()
    return token


def build_share_url(token: str | None, base_url: str = "") -> str | None:
    if not token:
        return None
    return f"{base_url.rstrip('/')}/share/{token}" if base_url else f"/share/{token}"


def verify_checkin_code(activity: Activity, submitted: str) -> str:
    """返回 OK / WRONG / EXPIRED（旧码）。"""
    from app.core.security import constant_time_equals

    if not activity.checkin_code:
        return "EXPIRED"
    if constant_time_equals(submitted, activity.checkin_code):
        return "OK"
    return "WRONG"


def is_expired_code(db: Session, activity_id: int, submitted: str) -> bool:
    from app.core.security import constant_time_equals

    rows = db.scalars(
        select(ActivityCheckinCode.code).where(
            ActivityCheckinCode.activity_id == activity_id,
            ActivityCheckinCode.is_current.is_(False),
        )
    ).all()
    return any(constant_time_equals(submitted, code) for code in rows)


def ensure_not_cancelled(activity: Activity) -> None:
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消")


def ensure_published(activity: Activity) -> None:
    if activity.status in (ActivityStatus.DRAFT,):
        fail(ErrorCode.ILLEGAL_STATE, "活动尚未发布")


def audit_mode(activity: Activity) -> str:
    return AuditMode.MANUAL if activity.need_audit else AuditMode.NONE
