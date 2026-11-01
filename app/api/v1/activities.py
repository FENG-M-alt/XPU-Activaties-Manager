"""活动浏览、报名与签到（U-08 ~ U-11、U-15）。

报名人数按 registrations 实时聚合（数据库文档 4.3），不使用冗余计数列。
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, case, func, or_, select

from app.config import settings
from app.core.deps import DbSession, OptionalUser
from app.core.enums import OCCUPYING_STATUSES, ActivityStatus, RegistrationStatus
from app.core.errors import ErrorCode, fail
from app.core.guards import enforce_rate, idempotency_key, remember, replay
from app.core.responses import Pagination, ok, page_data
from app.core.timeutil import now_local, time_range_bounds, to_iso
from app.models.activity import Activity
from app.models.registration import Registration
from app.schemas.requests import CheckinIn
from app.schemas.serializers import activity_detail, activity_list_item, api_base
from app.services import checkin as checkin_service
from app.services import registration as registration_service
from app.services.activity import checkin_window as activity_checkin_window
from app.services.activity import is_signup_open, remaining_map, sync_activity
from app.services.logs import client_ip, client_ua, write_log

router = APIRouter(tags=["用户端-活动"])

VISIBLE_STATUSES = (
    ActivityStatus.PUBLISHED,
    ActivityStatus.REGISTERING,
    ActivityStatus.ONGOING,
    ActivityStatus.FINISHED,
)


def _occupancy_subquery():
    return (
        select(Registration.activity_id.label("aid"), func.count().label("cnt"))
        .where(Registration.status.in_(OCCUPYING_STATUSES))
        .group_by(Registration.activity_id)
        .subquery()
    )


def _signup_open_clause(now, occ):
    """可报名：状态允许 + 在报名窗口内 + 未满员。"""
    return and_(
        Activity.status.in_([ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING]),
        or_(Activity.signup_start.is_(None), Activity.signup_start <= now),
        or_(Activity.signup_end.is_(None), Activity.signup_end >= now),
        func.coalesce(occ.c.cnt, 0) < Activity.capacity,
    )


@router.get("/activities", summary="U-08 活动列表")
def list_activities(
    db: DbSession,
    user: OptionalUser,
    keyword: str | None = Query(None, description="匹配活动名称、地点、主办单位"),
    type_id: int | None = Query(None, description="活动类型（dict_items.id）"),
    time_range: Literal["week", "month", "term", "custom"] | None = Query(None),
    start_date: date | None = Query(None, description="time_range=custom 时必填"),
    end_date: date | None = Query(None, description="time_range=custom 时必填"),
    signup_state: Literal["open", "full"] | None = Query(None),
    sort: Literal["latest", "soonest", "popular"] | None = Query(None),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """支持关键字、类型、状态、排序与分页；仅返回已发布及之后状态的活动。"""
    now = now_local()
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_STUDENT)

    if time_range == "custom" and (start_date is None or end_date is None):
        fail(
            ErrorCode.PARAM_INVALID,
            "time_range=custom 时 start_date 与 end_date 必填",
            data={"field": "start_date", "reason": "custom 模式下必填"},
        )

    occ = _occupancy_subquery()
    conditions = [Activity.deleted_at.is_(None), Activity.status.in_(VISIBLE_STATUSES)]

    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(Activity.title.like(like), Activity.location.like(like), Activity.host.like(like))
        )
    if type_id:
        conditions.append(Activity.type_id == type_id)

    bound_start, bound_end = time_range_bounds(time_range, start_date, end_date, now)
    if bound_start:
        conditions.append(Activity.start_time >= bound_start)
    if bound_end:
        conditions.append(Activity.start_time <= bound_end)

    if signup_state == "open":
        conditions.append(_signup_open_clause(now, occ))
    elif signup_state == "full":
        conditions.append(func.coalesce(occ.c.cnt, 0) >= Activity.capacity)

    base = (
        select(Activity, func.coalesce(occ.c.cnt, 0).label("occ_count"))
        .outerjoin(occ, occ.c.aid == Activity.id)
        .where(*conditions)
    )
    total = int(
        db.scalar(select(func.count()).select_from(base.subquery())) or 0
    )

    if sort == "latest":
        order = (Activity.created_at.desc(),)
    elif sort == "soonest":
        order = (Activity.start_time.asc(),)
    elif sort == "popular":
        order = (func.coalesce(occ.c.cnt, 0).desc(), Activity.start_time.asc())
    else:
        open_first = case((_signup_open_clause(now, occ), 0), else_=1)
        order = (open_first, Activity.start_time.asc())

    rows = db.execute(
        base.order_by(*order).offset(pagination.offset).limit(pagination.page_size)
    ).all()

    registrations: dict[int, Registration] = {}
    ids = [a.id for a, _ in rows]
    if user is not None and ids:
        found = db.scalars(
            select(Registration).where(
                Registration.user_id == user.id,
                Registration.activity_id.in_(ids),
                Registration.status.notin_(
                    [RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]
                ),
            )
        ).all()
        registrations = {r.activity_id: r for r in found}

    items = [
        activity_list_item(
            activity,
            now=now,
            registration=registrations.get(activity.id),
            remaining=max(activity.capacity - int(count), 0),
            registered_count=int(count),
        )
        for activity, count in rows
    ]
    return ok(
        page_data(items, total, pagination.page, pagination.page_size),
        message=pagination.message(),
    )


def _load_activity(db: DbSession, activity_id: int, now) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        fail(ErrorCode.NOT_FOUND, "活动不存在或已下架")
    if sync_activity(db, activity, now):
        db.commit()
        db.refresh(activity)
    return activity


@router.get("/activities/{activity_id}", summary="U-09 活动详情")
def get_activity(activity_id: int, db: DbSession, user: OptionalUser, request: Request):
    """活动详情与当前用户报名状态；未登录时 my_registration 为 null。"""
    now = now_local()
    activity = _load_activity(db, activity_id, now)

    registration = None
    if user is not None:
        registration = db.scalars(
            select(Registration).where(
                Registration.activity_id == activity.id,
                Registration.user_id == user.id,
                Registration.status.notin_(
                    [RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]
                ),
            )
        ).first()

    counts = remaining_map(db, [activity.id])
    used = counts.get(activity.id, 0)
    return ok(
        activity_detail(
            activity,
            now=now,
            registration=registration,
            remaining=max(activity.capacity - used, 0),
            registered_count=used,
            base_url=api_base(request),
        )
    )


@router.post("/activities/{activity_id}/registrations", summary="U-11 提交报名")
def create_registration(activity_id: int, db: DbSession, user: OptionalUser, request: Request):
    """按接口文档 4.6 校验顺序执行；5 秒内相同请求返回首次结果。"""
    if user is None:
        fail(ErrorCode.UNAUTHORIZED)

    enforce_rate("signup", str(user.id), settings.RATE_WRITE)
    key = idempotency_key("user", user.id, request.url.path, None)
    hit, cached = replay(key)
    if hit:
        return ok(cached, message="重复提交，返回首次结果")

    now = now_local()
    activity = _load_activity(db, activity_id, now)

    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消")
    if not is_signup_open(activity, now):
        fail(ErrorCode.SIGNUP_WINDOW_CLOSED)
    if registration_service.find_active_registration(db, activity.id, user.id):
        fail(ErrorCode.DUPLICATE_SIGNUP)

    registration = registration_service.create_registration(db, activity, user, now=now)

    window_start, window_end = activity_checkin_window(activity)
    data = {
        "registration_id": registration.id,
        "status": registration.status,
        "need_audit": bool(activity.need_audit),
        "checkin_window": {"start": to_iso(window_start), "end": to_iso(window_end)},
        "signup_at": to_iso(registration.signup_at),
    }
    write_log(
        db,
        action="SIGNUP",
        operator_type="STUDENT",
        operator_id=user.id,
        target_type="activities",
        target_id=activity.id,
        detail={"registration_id": registration.id, "status": registration.status},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    remember(key, data)

    message = "报名成功，等待审核" if activity.need_audit else "报名成功"
    return ok(data, message=message)


@router.post("/activities/{activity_id}/checkin", summary="U-15 提交签到")
def submit_checkin(
    activity_id: int, payload: CheckinIn, db: DbSession, user: OptionalUser, request: Request
):
    """提交 6 位签到码；连续输错 5 次锁定 10 分钟。"""
    if user is None:
        fail(ErrorCode.UNAUTHORIZED)

    enforce_rate("checkin", str(user.id), settings.RATE_CHECKIN)
    key = idempotency_key("user", user.id, request.url.path, payload.model_dump())
    hit, cached = replay(key)
    if hit:
        return ok(cached, message="重复提交，返回首次结果")

    now = now_local()
    activity = _load_activity(db, activity_id, now)

    data = checkin_service.submit_checkin(
        db, activity, user, payload.code, ip=client_ip(request), now=now
    )
    write_log(
        db,
        action="CHECKIN",
        operator_type="STUDENT",
        operator_id=user.id,
        target_type="activities",
        target_id=activity.id,
        detail={"method": "CODE", "is_late": data["is_late"]},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    remember(key, data)
    return ok(data, message="签到成功")
