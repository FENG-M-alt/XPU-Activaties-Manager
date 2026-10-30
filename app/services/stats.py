"""统计聚合（文档 4.10 / 5.2 / 5.11）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import Select, case, distinct, func, select
from sqlalchemy.orm import Session

from app.core.enums import (
    COUNTED_STATUSES,
    ActivityStatus,
    RegistrationStatus,
)
from app.core.timeutil import (
    day_end,
    day_start,
    month_range,
    now_local,
    term_bounds,
    to_iso,
)
from app.models.activity import Activity, ActivityType
from app.models.registration import Registration
from app.models.student import Student


CHECKED_IN_SET = (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)
ABSENT_SET = (RegistrationStatus.ABSENT,)
EFFECTIVE_SET = (
    RegistrationStatus.APPROVED,
    RegistrationStatus.CHECKED_IN,
    RegistrationStatus.COMPLETED,
    RegistrationStatus.ABSENT,
)


def rate_text(numerator: int, denominator: int) -> str:
    """百分比，保留 1 位小数，分母为 0 时为'—'（文档 6.5）。"""
    if not denominator:
        return "—"
    return f"{numerator / denominator * 100:.1f}%"


def _count(db: Session, stmt: Select) -> int:
    return int(db.scalar(stmt) or 0)


def activity_visible_filter() -> list:
    """统计仅计入已发布及之后状态的活动。"""
    return [
        Activity.is_deleted.is_(False),
        Activity.status.notin_([ActivityStatus.DRAFT]),
    ]


# ---------------------------------------------------------------- 学生个人统计
def student_stats(db: Session, student_id: int, now: datetime | None = None) -> dict:
    now = now or now_local()
    term_start, term_end = term_bounds(now)

    base = (
        select(Registration, Activity)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(
            Registration.student_id == student_id,
            Registration.status.in_(CHECKED_IN_SET),
            Activity.is_deleted.is_(False),
        )
        .order_by(Activity.start_time.desc())
    )
    rows = db.execute(base).all()

    total_activities = len({r.id for r, _ in rows})
    total_hours = sum(float(r.hours_awarded or 0) for r, _ in rows)

    # 签到率：分母为已签到+缺席
    absent = _count(
        db,
        select(func.count()).select_from(Registration).where(
            Registration.student_id == student_id,
            Registration.status.in_(ABSENT_SET),
        ),
    )
    checked = total_activities
    term_count = sum(
        1 for _, a in rows if a.start_time and term_start <= a.start_time <= term_end
    )

    summary = {
        "activity_count": total_activities,
        "hours_total": round(total_hours, 1),
        "checkin_rate": rate_text(checked, checked + absent),
        "term_activity_count": term_count,
        "absent_count": absent,
    }

    # 类型分布
    type_rows = db.execute(
        select(
            ActivityType.id,
            ActivityType.name,
            func.count(distinct(Activity.id)),
            func.coalesce(func.sum(Registration.hours_awarded), 0),
        )
        .join(Activity, Activity.type_id == ActivityType.id)
        .join(Registration, Registration.activity_id == Activity.id)
        .where(
            Registration.student_id == student_id,
            Registration.status.in_(CHECKED_IN_SET),
            Activity.is_deleted.is_(False),
        )
        .group_by(ActivityType.id, ActivityType.name)
        .order_by(func.count(distinct(Activity.id)).desc())
    ).all()
    type_distribution = [
        {"type_id": tid, "type_name": name, "count": int(cnt), "hours": round(float(hours or 0), 1)}
        for tid, name, cnt, hours in type_rows
    ]

    # 近 6 个月趋势
    start, _ = month_range(now.date(), 6)
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        month_expr = func.strftime("%Y-%m", Activity.start_time)
    elif db.bind is not None and db.bind.dialect.name in ("mysql", "mariadb"):
        month_expr = func.date_format(Activity.start_time, "%Y-%m")
    else:
        month_expr = func.to_char(Activity.start_time, "YYYY-MM")
    trend_rows = db.execute(
        select(month_expr, func.count(distinct(Activity.id)))
        .join(Registration, Registration.activity_id == Activity.id)
        .where(
            Registration.student_id == student_id,
            Registration.status.in_(CHECKED_IN_SET),
            Activity.start_time >= start,
            Activity.is_deleted.is_(False),
        )
        .group_by(month_expr)
        .order_by(month_expr)
    ).all()
    trend_map = {str(k): int(v) for k, v in trend_rows}
    monthly_trend = []
    cursor = date(start.year, start.month, 1)
    for _ in range(6):
        key = f"{cursor.year:04d}-{cursor.month:02d}"
        monthly_trend.append({"month": key, "count": trend_map.get(key, 0)})
        cursor = date(cursor.year + (1 if cursor.month == 12 else 0), 1 if cursor.month == 12 else cursor.month + 1, 1)

    details = [
        {
            "activity_id": a.id,
            "title": a.title,
            "start_time": to_iso(a.start_time),
            "end_time": to_iso(a.end_time),
            "location": a.location,
            "hours": float(r.hours_awarded or 0),
            "status": r.status,
            "checkin_at": to_iso(r.checkin_at),
            "is_late": r.is_late,
        }
        for r, a in rows
    ]

    return {
        "summary": summary,
        "type_distribution": type_distribution,
        "monthly_trend": monthly_trend,
        "details": details,
    }


# ---------------------------------------------------------------- 管理端工作台
def dashboard(db: Session, scope_conditions: list, now: datetime | None = None) -> dict:
    now = now or now_local()
    today_start, today_end = day_start(now), day_end(now)
    term_start, term_end = term_bounds(now)

    activity_filter = [*activity_visible_filter(), *scope_conditions]

    ongoing = _count(
        db,
        select(func.count()).select_from(Activity).where(
            *activity_filter,
            Activity.start_time <= now,
            Activity.end_time >= now,
            Activity.status.in_([ActivityStatus.ONGOING, ActivityStatus.REGISTERING]),
        ),
    )
    pending_audit = _count(
        db,
        select(func.count()).select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*activity_filter, Registration.status == RegistrationStatus.PENDING),
    )
    today_checkin = _count(
        db,
        select(func.count()).select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(
            *activity_filter,
            Registration.checkin_at >= today_start,
            Registration.checkin_at <= today_end,
            Registration.status.in_(CHECKED_IN_SET),
        ),
    )
    term_participation = _count(
        db,
        select(func.count(distinct(Registration.student_id)))
        .select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(
            *activity_filter,
            Registration.status.in_(CHECKED_IN_SET),
            Activity.start_time >= term_start,
            Activity.start_time <= term_end,
        ),
    )

    metrics = {
        "ongoing_activity_count": ongoing,
        "pending_audit_count": pending_audit,
        "today_checkin_count": today_checkin,
        "term_participant_count": term_participation,
    }

    # 待办：待审核活动聚合、24 小时内开始的活动、异常签到
    pending_activities = db.execute(
        select(Activity.id, Activity.title, func.count(Registration.id))
        .join(Registration, Registration.activity_id == Activity.id)
        .where(*activity_filter, Registration.status == RegistrationStatus.PENDING)
        .group_by(Activity.id, Activity.title)
        .order_by(func.count(Registration.id).desc())
        .limit(10)
    ).all()
    starting_soon = db.scalars(
        select(Activity)
        .where(
            *activity_filter,
            Activity.start_time >= now,
            Activity.start_time <= now + timedelta(hours=24),
            Activity.status.in_([ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING]),
        )
        .order_by(Activity.start_time.asc())
        .limit(10)
    ).all()
    late_count = _count(
        db,
        select(func.count()).select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*activity_filter, Registration.status.in_(CHECKED_IN_SET), Registration.is_late.is_(True)),
    )

    todos = [
        {
            "type": "PENDING_AUDIT",
            "title": "待审核报名",
            "count": pending_audit,
            "items": [
                {"activity_id": aid, "activity_title": title, "pending_count": int(cnt)}
                for aid, title, cnt in pending_activities
            ],
        },
        {
            "type": "STARTING_SOON",
            "title": "24 小时内开始的活动",
            "count": len(starting_soon),
            "items": [
                {"activity_id": a.id, "title": a.title, "start_time": to_iso(a.start_time)}
                for a in starting_soon
            ],
        },
        {
            "type": "ABNORMAL_CHECKIN",
            "title": "异常签到（迟到）",
            "count": late_count,
            "items": [],
        },
    ]

    # 近 30 天报名与签到人次
    since = day_start(now) - timedelta(days=29)
    signup_rows = db.execute(
        select(func.date(Registration.signup_at), func.count())
        .select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*activity_filter, Registration.signup_at >= since)
        .group_by(func.date(Registration.signup_at))
    ).all()
    checkin_rows = db.execute(
        select(func.date(Registration.checkin_at), func.count())
        .select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*activity_filter, Registration.checkin_at >= since, Registration.status.in_(CHECKED_IN_SET))
        .group_by(func.date(Registration.checkin_at))
    ).all()
    signup_map = {str(k): int(v) for k, v in signup_rows}
    checkin_map = {str(k): int(v) for k, v in checkin_rows}
    trend = []
    for offset in range(30):
        d = (since + timedelta(days=offset)).date()
        key = d.isoformat()
        trend.append({"date": key, "signup_count": signup_map.get(key, 0), "checkin_count": checkin_map.get(key, 0)})

    return {"metrics": metrics, "todos": todos, "trend": trend}


# ---------------------------------------------------------------- 统计总览
def overview(db: Session, scope_conditions: list, *, range_start: datetime | None, range_end: datetime | None, college: str | None, type_id: int | None, now: datetime | None = None) -> dict:
    now = now or now_local()
    filters = [*activity_visible_filter(), *scope_conditions]
    if range_start:
        filters.append(Activity.start_time >= range_start)
    if range_end:
        filters.append(Activity.start_time <= range_end)
    if type_id:
        filters.append(Activity.type_id == type_id)
    if college:
        filters.append(Activity.college == college)

    activity_count = _count(db, select(func.count()).select_from(Activity).where(*filters))

    base_filter = [Registration.activity_id == Activity.id, *filters]

    signup_count = _count(
        db,
        select(func.count()).select_from(Registration).join(Activity, Activity.id == Registration.activity_id).where(*base_filter),
    )
    checkin_count = _count(
        db,
        select(func.count()).select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*base_filter, Registration.status.in_(CHECKED_IN_SET)),
    )
    absent_count = _count(
        db,
        select(func.count()).select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*base_filter, Registration.status.in_(ABSENT_SET)),
    )
    participant_count = _count(
        db,
        select(func.count(distinct(Registration.student_id)))
        .select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*base_filter, Registration.status.in_(CHECKED_IN_SET)),
    )
    hours_total = float(
        db.scalar(
            select(func.coalesce(func.sum(Registration.hours_awarded), 0))
            .select_from(Registration)
            .join(Activity, Activity.id == Registration.activity_id)
            .where(*base_filter, Registration.status.in_(CHECKED_IN_SET))
        )
        or 0
    )

    # 趋势（按天，默认近 30 天；有 range 参数时用 range）
    trend_start = range_start or (day_start(now) - timedelta(days=29))
    trend_end = range_end or day_end(now)
    trend_rows = db.execute(
        select(
            func.date(Registration.signup_at),
            func.count(),
        )
        .select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*base_filter, Registration.signup_at >= trend_start, Registration.signup_at <= trend_end)
        .group_by(func.date(Registration.signup_at))
        .order_by(func.date(Registration.signup_at))
    ).all()
    checkin_rows = db.execute(
        select(func.date(Registration.checkin_at), func.count())
        .select_from(Registration)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*base_filter, Registration.checkin_at >= trend_start, Registration.checkin_at <= trend_end, Registration.status.in_(CHECKED_IN_SET))
        .group_by(func.date(Registration.checkin_at))
        .order_by(func.date(Registration.checkin_at))
    ).all()
    sig = {str(k): int(v) for k, v in trend_rows}
    chk = {str(k): int(v) for k, v in checkin_rows}
    keys = sorted(set(sig) | set(chk))
    trend = [{"date": k, "signup_count": sig.get(k, 0), "checkin_count": chk.get(k, 0)} for k in keys]

    type_rows = db.execute(
        select(ActivityType.id, ActivityType.name, func.count(distinct(Activity.id)), func.count(Registration.id))
        .select_from(Activity)
        .outerjoin(ActivityType, ActivityType.id == Activity.type_id)
        .outerjoin(Registration, Registration.activity_id == Activity.id)
        .where(*filters)
        .group_by(ActivityType.id, ActivityType.name)
    ).all()
    type_distribution = [
        {"type_id": tid, "type_name": name or "未分类", "activity_count": int(ac), "signup_count": int(sc)}
        for tid, name, ac, sc in type_rows
    ]

    college_rows = db.execute(
        select(Student.college, func.count(distinct(Student.id)), func.count(Registration.id))
        .select_from(Registration)
        .join(Student, Student.id == Registration.student_id)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*base_filter, Registration.status.in_(CHECKED_IN_SET))
        .group_by(Student.college)
        .order_by(func.count(Registration.id).desc())
        .limit(20)
    ).all()
    college_ranking = [
        {"college": c or "未填写", "participant_count": int(p), "checkin_count": int(cc)}
        for c, p, cc in college_rows
    ]

    return {
        "metrics": {
            "activity_count": activity_count,
            "signup_count": signup_count,
            "checkin_count": checkin_count,
            "absent_count": absent_count,
            "checkin_rate": rate_text(checkin_count, checkin_count + absent_count),
            "participant_count": participant_count,
            "hours_total": round(hours_total, 1),
        },
        "trend": trend,
        "type_distribution": type_distribution,
        "college_ranking": college_ranking,
    }


def stats_by_activity(db: Session, scope_conditions: list, *, page_offset: int, limit: int, keyword: str | None = None, type_id: int | None = None, range_start: datetime | None = None, range_end: datetime | None = None) -> tuple[list[dict], int]:
    filters = [*activity_visible_filter(), *scope_conditions]
    if keyword:
        filters.append(Activity.title.like(f"%{keyword}%"))
    if type_id:
        filters.append(Activity.type_id == type_id)
    if range_start:
        filters.append(Activity.start_time >= range_start)
    if range_end:
        filters.append(Activity.start_time <= range_end)

    total = _count(db, select(func.count()).select_from(Activity).where(*filters))
    rows = db.execute(
        select(
            Activity.id,
            Activity.title,
            Activity.start_time,
            Activity.status,
            Activity.capacity,
            func.count(Registration.id),
            func.sum(case((Registration.status.in_(CHECKED_IN_SET), 1), else_=0)),
            func.sum(case((Registration.status.in_(ABSENT_SET), 1), else_=0)),
            func.coalesce(func.sum(Registration.hours_awarded), 0),
        )
        .select_from(Activity)
        .outerjoin(Registration, Registration.activity_id == Activity.id)
        .where(*filters)
        .group_by(Activity.id, Activity.title, Activity.start_time, Activity.status, Activity.capacity)
        .order_by(Activity.start_time.desc())
        .offset(page_offset)
        .limit(limit)
    ).all()

    items = []
    for aid, title, start, status, capacity, signup, checked, absent, hours in rows:
        checked = int(checked or 0)
        absent = int(absent or 0)
        items.append(
            {
                "activity_id": aid,
                "title": title,
                "start_time": to_iso(start),
                "status": status,
                "capacity": capacity,
                "signup_count": int(signup or 0),
                "approved_count": checked + absent,
                "checkin_count": checked,
                "absent_count": absent,
                "checkin_rate": rate_text(checked, checked + absent),
                "hours_total": round(float(hours or 0), 1),
            }
        )
    return items, total


def stats_by_student(db: Session, scope_conditions: list, *, page_offset: int, limit: int, keyword: str | None = None, college: str | None = None, grade: str | None = None) -> tuple[list[dict], int]:
    filters = [*activity_visible_filter(), *scope_conditions]
    student_filters = []
    if keyword:
        student_filters.append((Student.name.like(f"%{keyword}%")) | (Student.student_no.like(f"%{keyword}%")))
    if college:
        student_filters.append(Student.college == college)
    if grade:
        student_filters.append(Student.grade == grade)

    subq = (
        select(
            Registration.student_id.label("sid"),
            func.count(distinct(Registration.activity_id)).label("cnt"),
            func.coalesce(func.sum(Registration.hours_awarded), 0).label("hours"),
            func.max(Registration.checkin_at).label("last_checkin"),
        )
        .join(Activity, Activity.id == Registration.activity_id)
        .where(Registration.status.in_(CHECKED_IN_SET), *filters, *student_filters)
        .group_by(Registration.student_id)
        .subquery()
    )

    total = _count(
        db,
        select(func.count()).select_from(subq),
    )
    rows = db.execute(
        select(Student.student_no, Student.name, Student.college, Student.class_name, subq.c.cnt, subq.c.hours, subq.c.last_checkin)
        .join(subq, subq.c.sid == Student.id)
        .order_by(subq.c.cnt.desc())
        .offset(page_offset)
        .limit(limit)
    ).all()
    items = [
        {
            "student_no": no,
            "name": name,
            "college": college_,
            "class_name": class_name,
            "activity_count": int(cnt or 0),
            "hours_total": round(float(hours or 0), 1),
            "last_checkin_at": to_iso(last),
        }
        for no, name, college_, class_name, cnt, hours, last in rows
    ]
    return items, total


def stats_by_college(db: Session, scope_conditions: list, *, range_start: datetime | None = None, range_end: datetime | None = None) -> list[dict]:
    filters = [*activity_visible_filter(), *scope_conditions]
    if range_start:
        filters.append(Activity.start_time >= range_start)
    if range_end:
        filters.append(Activity.start_time <= range_end)

    registered = dict(
        db.execute(select(Student.college, func.count()).group_by(Student.college)).all()
    )
    rows = db.execute(
        select(
            Student.college,
            func.count(distinct(Registration.student_id)),
            func.count(distinct(Activity.id)),
            func.count(distinct(Registration.id)),
            func.coalesce(func.sum(Registration.hours_awarded), 0),
            func.sum(case((Registration.status.in_(CHECKED_IN_SET), 1), else_=0)),
            func.sum(case((Registration.status.in_(ABSENT_SET), 1), else_=0)),
        )
        .select_from(Registration)
        .join(Student, Student.id == Registration.student_id)
        .join(Activity, Activity.id == Registration.activity_id)
        .where(*filters)
        .group_by(Student.college)
        .order_by(func.count(distinct(Registration.student_id)).desc())
    ).all()

    items = []
    for college_, participants, activities, signups, hours, checked, absent in rows:
        checked = int(checked or 0)
        absent = int(absent or 0)
        total_students = int(registered.get(college_, 0) or 0)
        items.append(
            {
                "college": college_ or "未填写",
                "student_count": total_students,
                "participant_count": int(participants or 0),
                "participation_rate": rate_text(int(participants or 0), total_students),
                "activity_count": int(activities or 0),
                "signup_count": int(signups or 0),
                "checkin_count": checked,
                "absent_count": absent,
                "hours_total": round(float(hours or 0), 1),
                "hours_per_capita": round(float(hours or 0) / participants, 1) if participants else 0.0,
                "checkin_rate": rate_text(checked, checked + absent),
            }
        )
    return items


# ---------------------------------------------------------------- 学生参与记录
def student_records(db: Session, student_id: int, *, page_offset: int, limit: int) -> tuple[list[dict], int]:
    total = _count(
        db,
        select(func.count()).select_from(Registration).where(Registration.student_id == student_id),
    )
    rows = db.scalars(
        select(Registration)
        .where(Registration.student_id == student_id)
        .order_by(Registration.signup_at.desc())
        .offset(page_offset)
        .limit(limit)
    ).all()
    items = [
        {
            "registration_id": r.id,
            "activity_id": r.activity_id,
            "activity_title": r.activity.title if r.activity else None,
            "activity_start_time": to_iso(r.activity.start_time) if r.activity else None,
            "status": r.status,
            "signup_at": to_iso(r.signup_at),
            "checkin_at": to_iso(r.checkin_at),
            "checkin_method": r.checkin_method,
            "is_late": r.is_late,
            "hours": float(r.hours_awarded or 0),
        }
        for r in rows
    ]
    return items, total


def checkin_board(db: Session, activity: Activity, now: datetime | None = None) -> dict:
    """签到看板（文档 A-20）：轮询接口，服务端不做推送。"""
    from app.core.security import mask_student_no

    now = now or now_local()
    rows = db.scalars(
        select(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
        )
        .order_by(Registration.checkin_at.desc())
    ).all()

    expected = len(rows)
    checked = [r for r in rows if r.status in CHECKED_IN_SET]
    late = [r for r in checked if r.is_late]
    unchecked = [r for r in rows if r.status == RegistrationStatus.APPROVED]

    recent = [
        {
            "student_no": mask_student_no(r.student.student_no if r.student else None),
            "name": r.student.name if r.student else None,
            "checkin_at": to_iso(r.checkin_at),
            "checkin_method": r.checkin_method,
            "is_late": r.is_late,
        }
        for r in checked[:20]
    ]
    unchecked_items = [
        {
            "student_no": r.student.student_no if r.student else None,
            "name": r.student.name if r.student else None,
            "college": r.student.college if r.student else None,
            "class_name": r.student.class_name if r.student else None,
            "status": r.status,
        }
        for r in unchecked
    ]

    return {
        "expected": expected,
        "checked_in": len(checked),
        "checkin_rate": rate_text(len(checked), expected),
        "late_count": len(late),
        "recent": recent,
        "unchecked": unchecked_items,
        "updated_at": to_iso(now),
    }
