"""首页聚合（U-07）。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select, or_

from app.core.deps import DbSession, OptionalStudent
from app.core.enums import ActivityStatus, RegistrationStatus
from app.core.responses import ok
from app.core.timeutil import now_local
from app.models.activity import Activity
from app.models.notice import Notice
from app.models.registration import Registration
from app.schemas.serializers import activity_list_item, notice_out
from app.services.activity import checkin_window

router = APIRouter(tags=["用户端-首页"])

VISIBLE_STATUSES = (
    ActivityStatus.PUBLISHED,
    ActivityStatus.REGISTERING,
    ActivityStatus.ONGOING,
)


def _current_notice(db: DbSession, now):
    rows = db.scalars(
        select(Notice)
        .where(
            Notice.is_active.is_(True),
            Notice.is_deleted.is_(False),
            or_(Notice.start_at.is_(None), Notice.start_at <= now),
            or_(Notice.end_at.is_(None), Notice.end_at >= now),
        )
        .order_by(Notice.is_pinned.desc(), Notice.created_at.desc())
    ).all()
    for notice in rows:
        if notice.is_effective(now):
            return notice
    return None


@router.get("/home", summary="U-07 首页聚合")
def home(db: DbSession, student: OptionalStudent):
    """公告、进行中与即将开始活动、待办数量。未登录访问时 todo 为 0。"""
    now = now_local()

    notice = _current_notice(db, now)

    ongoing_rows = db.scalars(
        select(Activity)
        .where(
            Activity.is_deleted.is_(False),
            Activity.status.in_([*VISIBLE_STATUSES, ActivityStatus.FINISHED]),
            Activity.start_time <= now,
            Activity.end_time >= now,
        )
        .order_by(Activity.start_time.asc())
        .limit(5)
    ).all()

    upcoming_rows = db.scalars(
        select(Activity)
        .where(
            Activity.is_deleted.is_(False),
            Activity.status.in_(VISIBLE_STATUSES),
            Activity.start_time > now,
        )
        .order_by(Activity.start_time.asc())
        .limit(5)
    ).all()

    todo = {"pending_registrations": 0, "checkin_available": 0}
    if student is not None:
        todo["pending_registrations"] = int(
            db.scalar(
                select(func.count())
                .select_from(Registration)
                .where(
                    Registration.student_id == student.id,
                    Registration.status == RegistrationStatus.PENDING,
                )
            )
            or 0
        )
        todo["checkin_available"] = 0
        for registration, activity in db.execute(
            select(Registration, Activity)
            .join(Activity, Activity.id == Registration.activity_id)
            .where(
                Registration.student_id == student.id,
                Registration.status == RegistrationStatus.APPROVED,
                Activity.is_deleted.is_(False),
            )
        ).all():
            window_start, window_end = checkin_window(activity)
            if window_start and now < window_start:
                continue
            if window_end and now > window_end:
                continue
            todo["checkin_available"] += 1

    return ok(
        {
            "notice": notice_out(notice) if notice else None,
            "ongoing": [activity_list_item(a, now=now) for a in ongoing_rows],
            "upcoming": [activity_list_item(a, now=now) for a in upcoming_rows],
            "todo": todo,
            "server_time": now.isoformat() + "+08:00",
        }
    )
