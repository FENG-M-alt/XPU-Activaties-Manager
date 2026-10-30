"""活动浏览、报名与签到（U-08 ~ U-11、U-15）。"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, case, func, or_, select

from app.config import settings
from app.core.deps import DbSession, OptionalStudent
from app.core.enums import ActivityStatus, RegistrationStatus, SignupState
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
from app.services.activity import is_signup_open, sync_activity
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["用户端-活动"])

VISIBLE_STATUSES = (
    ActivityStatus.PUBLISHED,
    ActivityStatus.REGISTERING,
    ActivityStatus.ONGOING,
    ActivityStatus.FINISHED,
)


def _signup_open_clause(now):
    return and_(
        Activity.status.in_([ActivityStatus.PUBLISHED, ActivityStatus.REGISTERING]),
        or_(Activity.signup_start.is_(None), Activity.signup_start <= now),
        or_(Activity.signup_end.is_(None), Activity.signup_end >= now),
        Activity.registered_count < Activity.capacity,
    )


@router.get("/activities", summary="U-08 活动列表")
def list_activities(
    db: DbSession,
    student: OptionalStudent,
    keyword: str | None = Query(None, description="匹配活动名称、地点、主办单位"),
    type_id: int | None = Query(None, description="活动类型"),
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
        fail(ErrorCode.PARAM_INVALID, "time_range=custom 时 start_date 与 end_date 必填",
             data={"field": "start_date", "reason": "custom 模式下必填"})

    conditions = [Activity.is_deleted.is_(False), Activity.status.in_(VISIBLE_STATUSES)]

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
        conditions.append(_signup_open_clause(now))
    elif signup_state == "full":
        conditions.append(Activity.registered_count >= Activity.capacity)

    total = int(db.scalar(select(func.count()).select_from(Activity).where(*conditions)) or 0)

    if sort == "latest":
        order = (Activity.created_at.desc(),)
    elif sort == "soonest":
        order = (Activity.start_time.asc(),)
    elif sort == "popular":
        order = (Activity.registered_count.desc(), Activity.start_time.asc())
    else:
        # 默认可报名优先 + 开始时间升序
        open_first = case((_signup_open_clause(now), 0), else_=1)
        order = (open_first, Activity.start_time.asc())

    rows = db.scalars(
        select(Activity)
        .where(*conditions)
        .order_by(*order)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    registrations: dict[int, Registration] = {}
    if student is not None and rows:
        found = db.scalars(
            select(Registration).where(
                Registration.student_id == student.id,
                Registration.activity_id.in_([a.id for a in rows]),
                Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
            )
        ).all()
        registrations = {r.activity_id: r for r in found}

    items = [activity_list_item(a, now=now, registration=registrations.get(a.id)) for a in rows]
    return ok(page_data(items, total, pagination.page, pagination.page_size), message=pagination.message())


def _load_activity(db: DbSession, activity_id: int, now) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None or activity.is_deleted:
        fail(ErrorCode.NOT_FOUND, "活动不存在或已下架")
    if sync_activity(db, activity, now):
        db.commit()
        db.refresh(activity)
    return activity


@router.get("/activities/{activity_id}", summary="U-09 活动详情")
def get_activity(activity_id: int, db: DbSession, student: OptionalStudent, request: Request):
    """活动详情与当前用户报名状态；未登录时 my_registration 为 null。"""
    now = now_local()
    activity = _load_activity(db, activity_id, now)

    registration = None
    if student is not None:
        registration = db.scalars(
            select(Registration).where(
                Registration.activity_id == activity.id,
                Registration.student_id == student.id,
                Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED]),
            )
        ).first()

    return ok(activity_detail(activity, now=now, registration=registration, base_url=api_base(request)))


@router.post("/activities/{activity_id}/registrations", summary="U-11 提交报名")
def create_registration(activity_id: int, db: DbSession, student: OptionalStudent, request: Request):
    """按文档 4.6 校验顺序执行；5 秒内相同请求返回首次结果。"""
    if student is None:
        fail(ErrorCode.UNAUTHORIZED)

    enforce_rate("signup", str(student.id), settings.RATE_WRITE)
    key = idempotency_key("student", student.id, request.url.path, None)
    hit, cached = replay(key)
    if hit:
        return ok(cached, message="重复提交，返回首次结果")

    now = now_local()
    activity = _load_activity(db, activity_id, now)

    # 2. 活动未被取消
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消")
    # 3. 当前处于报名窗口
    if not is_signup_open(activity, now):
        fail(ErrorCode.SIGNUP_WINDOW_CLOSED)
    # 4. 未重复报名
    if registration_service.find_active_registration(db, activity.id, student.id):
        fail(ErrorCode.DUPLICATE_SIGNUP)
    # 5. 有效报名数未达上限（在 occupy_slot 中原子校验）
    registration = registration_service.create_registration(db, activity, student, now=now)

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
        actor_type="student",
        actor_id=student.id,
        actor_name=student.name,
        action="SIGNUP",
        target_type="activity",
        target_id=activity.id,
        detail={"registration_id": registration.id, "status": registration.status},
        ip=client_ip(request),
    )
    db.commit()
    remember(key, data)

    message = "报名成功，等待审核" if activity.need_audit else "报名成功"
    return ok(data, message=message)


@router.post("/activities/{activity_id}/checkin", summary="U-15 提交签到")
def submit_checkin(activity_id: int, payload: CheckinIn, db: DbSession, student: OptionalStudent, request: Request):
    """提交 6 位签到码；连续输错 5 次锁定 10 分钟。"""
    if student is None:
        fail(ErrorCode.UNAUTHORIZED)

    enforce_rate("checkin", str(student.id), settings.RATE_CHECKIN)
    key = idempotency_key("student", student.id, request.url.path, payload.model_dump())
    hit, cached = replay(key)
    if hit:
        return ok(cached, message="重复提交，返回首次结果")

    now = now_local()
    activity = _load_activity(db, activity_id, now)

    data = checkin_service.submit_checkin(db, activity, student, payload.code, ip=client_ip(request), now=now)
    write_log(
        db,
        actor_type="student",
        actor_id=student.id,
        actor_name=student.name,
        action="CHECKIN",
        target_type="activity",
        target_id=activity.id,
        detail={"method": "CODE", "is_late": data["is_late"]},
        ip=client_ip(request),
    )
    db.commit()
    remember(key, data)
    return ok(data, message="签到成功")
