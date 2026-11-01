"""管理端活动管理（A-04 ~ A-13）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Body, Query, Request
from sqlalchemy import func, select, update

from app.config import settings
from app.core.deps import (
    CurrentAdmin,
    DbSession,
    activity_scope_conditions,
    ensure_activity_access,
)
from app.core.enums import OCCUPYING_STATUSES, ActivityStatus, RegistrationStatus
from app.core.errors import ErrorCode, fail
from app.core.responses import Pagination, ok, page_data
from app.core.timeutil import day_end, day_start, now_local, to_iso
from app.models.activity import Activity
from app.models.admin import Admin
from app.models.dict_item import DictItem
from app.models.registration import Registration
from app.schemas.requests import (
    ActivityCancelIn,
    ActivityCreateIn,
    ActivityReasonIn,
    ActivityUpdateIn,
    ResetCodeIn,
)
from app.schemas.serializers import (
    activity_stats,
    admin_activity_detail,
    admin_activity_item,
    api_base,
)
from app.services import activity as activity_service
from app.services import checkin as checkin_service
from app.services.logs import client_ip, client_ua, write_log

router = APIRouter(tags=["管理端-活动"])

CHECKED_IN_STATUSES = (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)


# ---------------------------------------------------------------- 辅助
def _load_activity(
    db: DbSession, admin: Admin, activity_id: int, now: datetime | None = None
) -> Activity:
    now = now or now_local()
    activity = db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        fail(ErrorCode.NOT_FOUND, "活动不存在")
    ensure_activity_access(admin, activity, db)
    if activity_service.sync_activity(db, activity, now):
        db.commit()
        db.refresh(activity)
    return activity


def _apply_create_fields(activity: Activity, payload: ActivityCreateIn) -> None:
    """把请求字段写入模型；college 等非本表字段被忽略。"""
    data = payload.model_dump(exclude_unset=True)
    for field in (
        "title",
        "type_id",
        "host",
        "organizer",
        "location",
        "cover_url",
        "description",
        "notice",
        "contact_name",
        "contact_phone",
        "signup_start",
        "signup_end",
        "start_time",
        "end_time",
    ):
        if field in data:
            setattr(activity, field, data[field])
    if data.get("capacity") is not None:
        activity.capacity = data["capacity"]
    if data.get("need_audit") is not None:
        activity.need_audit = data["need_audit"]
    if data.get("allow_cancel") is not None:
        activity.allow_cancel = data["allow_cancel"]
    if data.get("hours") is not None:
        activity.hours = Decimal(str(data["hours"]))
    if data.get("checkin_before") is not None:
        activity.checkin_before = data["checkin_before"]
    if data.get("checkin_after") is not None:
        activity.checkin_after = data["checkin_after"]


def _validate_times(activity: Activity) -> None:
    if activity.start_time and activity.end_time and activity.start_time >= activity.end_time:
        fail(
            ErrorCode.PARAM_INVALID,
            "活动结束时间须晚于开始时间",
            data={"field": "end_time", "reason": "须晚于开始时间"},
        )
    if activity.signup_start and activity.signup_end and activity.signup_start >= activity.signup_end:
        fail(
            ErrorCode.PARAM_INVALID,
            "报名结束时间须晚于报名开始时间",
            data={"field": "signup_end", "reason": "须晚于报名开始时间"},
        )
    if activity.signup_end and activity.start_time and activity.signup_end > activity.start_time:
        fail(
            ErrorCode.PARAM_INVALID,
            "报名截止时间不得晚于活动开始时间",
            data={"field": "signup_end", "reason": "不得晚于活动开始时间"},
        )


def _occupancy(db: DbSession, activity_ids: list[int]) -> dict[int, int]:
    """按活动聚合有效报名数（不设冗余列）。"""
    if not activity_ids:
        return {}
    rows = db.execute(
        select(Registration.activity_id, func.count())
        .where(
            Registration.activity_id.in_(activity_ids),
            Registration.status.in_(OCCUPYING_STATUSES),
        )
        .group_by(Registration.activity_id)
    ).all()
    return {aid: int(cnt) for aid, cnt in rows}


def _checkin_counts(db: DbSession, activity_ids: list[int]) -> dict[int, int]:
    if not activity_ids:
        return {}
    rows = db.execute(
        select(Registration.activity_id, func.count())
        .where(
            Registration.activity_id.in_(activity_ids),
            Registration.status.in_(CHECKED_IN_STATUSES),
        )
        .group_by(Registration.activity_id)
    ).all()
    return {aid: int(cnt) for aid, cnt in rows}


def _creator_names(db: DbSession, activities: list[Activity]) -> dict[int, str]:
    ids = {a.created_by for a in activities if a.created_by}
    if not ids:
        return {}
    rows = db.execute(select(Admin.id, Admin.name).where(Admin.id.in_(ids))).all()
    return {aid: name for aid, name in rows}



# ---------------------------------------------------------------- A-04 列表
@router.get("/activities", summary="A-04 活动列表")
def list_activities(
    db: DbSession,
    admin: CurrentAdmin,
    keyword: str | None = Query(None, description="活动名称或地点"),
    status: str | None = Query(None, description="六种活动状态"),
    type_id: int | None = Query(None),
    start_from: date | None = Query(None),
    start_to: date | None = Query(None),
    created_by: int | None = Query(None),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """多条件筛选；默认按创建时间倒序，按数据范围过滤。"""
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    conditions = [Activity.deleted_at.is_(None), *activity_scope_conditions(admin, db)]

    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(Activity.title.like(like) | Activity.location.like(like))
    if status:
        values = [s.strip().upper() for s in status.split(",") if s.strip()]
        invalid = [s for s in values if s not in set(ActivityStatus)]
        if invalid:
            fail(
                ErrorCode.PARAM_INVALID,
                f"不支持的 activity status: {invalid[0]}",
                data={"field": "status", "reason": "取值不合法"},
            )
        conditions.append(Activity.status.in_(values))
    if type_id:
        conditions.append(Activity.type_id == type_id)
    if start_from:
        conditions.append(Activity.start_time >= day_start(start_from))
    if start_to:
        conditions.append(Activity.start_time <= day_end(start_to))
    if created_by:
        conditions.append(Activity.created_by == created_by)

    total = int(db.scalar(select(func.count()).select_from(Activity).where(*conditions)) or 0)
    rows = list(
        db.scalars(
            select(Activity)
            .where(*conditions)
            .order_by(Activity.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
    )

    ids = [a.id for a in rows]
    occ = _occupancy(db, ids)
    checked = _checkin_counts(db, ids)
    names = _creator_names(db, rows)
    now = now_local()
    items = [
        admin_activity_item(
            activity,
            now=now,
            checkin_count=checked.get(activity.id, 0),
            registered_count=occ.get(activity.id, 0),
            creator_name=names.get(activity.created_by),
        )
        for activity in rows
    ]
    return ok(
        page_data(items, total, pagination.page, pagination.page_size), message=pagination.message()
    )


# ---------------------------------------------------------------- A-05 创建
@router.post("/activities", summary="A-05 新建活动（草稿）")
def create_activity(payload: ActivityCreateIn, db: DbSession, admin: CurrentAdmin, request: Request):
    """创建草稿，响应返回 id、version、status（DRAFT）。"""
    if payload.type_id and db.get(DictItem, payload.type_id) is None:
        fail(ErrorCode.PARAM_INVALID, "活动类型不存在", data={"field": "type_id", "reason": "不存在"})

    activity = Activity(
        title=payload.title,
        type_id=payload.type_id or 0,
        host=payload.host or "",
        location=payload.location or "",
        description=payload.description or "",
        contact_name=payload.contact_name or "",
        contact_phone=payload.contact_phone or "",
        signup_start=payload.signup_start or now_local(),
        signup_end=payload.signup_end or now_local(),
        start_time=payload.start_time or now_local(),
        end_time=payload.end_time or now_local(),
        status=ActivityStatus.DRAFT,
        version=1,
        capacity=0,
        need_audit=False,
        allow_cancel=True,
        hours=Decimal("1.0"),
        checkin_before=30,
        checkin_after=30,
        created_by=admin.id,
    )
    _apply_create_fields(activity, payload)
    if payload.start_time is None or payload.end_time is None:
        # 草稿允许留空，但模型列 NOT NULL，先用占位再由发布校验
        base = now_local() + timedelta(days=1)
        activity.start_time = activity.start_time or base
        activity.end_time = activity.end_time or (base + timedelta(hours=2))
    if payload.signup_start is None:
        activity.signup_start = now_local()
    if payload.signup_end is None:
        activity.signup_end = activity.start_time
    _validate_times(activity)

    db.add(activity)
    db.flush()
    write_log(
        db,
        action="ACTIVITY_CREATE",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"title": activity.title},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(
        {"id": activity.id, "version": activity.version, "status": activity.status},
        message="草稿已创建",
    )


# ---------------------------------------------------------------- A-06 详情
@router.get("/activities/{activity_id}", summary="A-06 活动详情")
def get_activity(activity_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    """返回全部字段，附签到码、分享链接与统计。"""
    activity = _load_activity(db, admin, activity_id)
    occ = _occupancy(db, [activity.id]).get(activity.id, 0)
    checked = _checkin_counts(db, [activity.id]).get(activity.id, 0)
    names = _creator_names(db, [activity])
    return ok(
        admin_activity_detail(
            activity,
            stats=activity_stats(db, activity),
            base_url=api_base(request),
            registered_count=occ,
            checkin_count=checked,
            creator_name=names.get(activity.created_by),
        )
    )


# ---------------------------------------------------------------- A-07 编辑
@router.patch("/activities/{activity_id}", summary="A-07 编辑活动")
def update_activity(
    activity_id: int, payload: ActivityUpdateIn, db: DbSession, admin: CurrentAdmin, request: Request
):
    """部分更新；version 不一致返回 40904。活动开始后仅允许修改结束时间、联系人与备注。"""
    now = now_local()
    activity = _load_activity(db, admin, activity_id, now)

    if payload.version != activity.version:
        fail(
            ErrorCode.ILLEGAL_STATE,
            f"活动已被他人修改（当前版本 {activity.version}），请刷新后重试",
        )

    changed = payload.model_dump(exclude_unset=True)
    changed.pop("version", None)
    if not changed:
        return ok({"id": activity.id, "version": activity.version}, message="无变更")

    started = bool(activity.start_time and now >= activity.start_time) or activity.status in (
        ActivityStatus.ONGOING,
        ActivityStatus.FINISHED,
    )
    if started:
        allowed = {"end_time", "contact_name", "contact_phone", "notice"}
        illegal = [k for k in changed if k not in allowed]
        if illegal:
            fail(
                ErrorCode.PARAM_INVALID,
                f"活动开始后仅允许修改结束时间、联系人与备注（不支持：{illegal[0]}）",
                data={"field": illegal[0], "reason": "活动开始后不可修改"},
            )

    if "capacity" in changed and changed["capacity"] is not None:
        used = _occupancy(db, [activity.id]).get(activity.id, 0)
        if changed["capacity"] < used:
            fail(
                ErrorCode.PARAM_INVALID,
                f"人数上限不得低于当前有效报名数（{used}）",
                data={"field": "capacity", "reason": "低于有效报名数"},
            )
    if "title" in changed and changed["title"] is None:
        changed.pop("title")
    if "type_id" in changed and changed["type_id"] and db.get(DictItem, changed["type_id"]) is None:
        fail(ErrorCode.PARAM_INVALID, "活动类型不存在", data={"field": "type_id", "reason": "不存在"})

    _apply_create_fields(activity, payload)
    _validate_times(activity)
    activity.version += 1
    db.add(activity)
    write_log(
        db,
        action="ACTIVITY_UPDATE",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"fields": list(changed.keys()), "version": activity.version},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    db.refresh(activity)
    return ok(
        {"id": activity.id, "version": activity.version, "status": activity.status},
        message="保存成功",
    )


# ---------------------------------------------------------------- A-08 发布
@router.post("/activities/{activity_id}/publish", summary="A-08 发布活动")
def publish_activity(activity_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    """发布前校验必填项与时间合法性，生成签到码与分享链接。"""
    now = now_local()
    activity = _load_activity(db, admin, activity_id, now)

    if activity.status != ActivityStatus.DRAFT:
        fail(ErrorCode.ILLEGAL_STATE, "仅草稿状态的活动可以发布")
    if not activity.title:
        fail(ErrorCode.PARAM_INVALID, "活动名称不能为空", data={"field": "title", "reason": "必填"})
    if not activity.type_id:
        fail(ErrorCode.PARAM_INVALID, "请选择活动类型", data={"field": "type_id", "reason": "必填"})
    if not activity.start_time or not activity.end_time:
        fail(
            ErrorCode.PARAM_INVALID,
            "请填写活动开始与结束时间",
            data={"field": "start_time", "reason": "必填"},
        )
    if activity.capacity < 1:
        fail(
            ErrorCode.PARAM_INVALID,
            "人数上限须在 1—9999 之间",
            data={"field": "capacity", "reason": "必填"},
        )
    _validate_times(activity)
    if activity.start_time <= now:
        fail(
            ErrorCode.PARAM_INVALID,
            "活动开始时间须晚于当前时间",
            data={"field": "start_time", "reason": "须晚于当前时间"},
        )

    if not activity.signup_start:
        activity.signup_start = now
    if not activity.signup_end:
        activity.signup_end = min(activity.start_time, activity.end_time)

    code = activity_service.issue_checkin_code(db, activity, admin_id=admin.id, reason="发布活动")
    token = activity_service.issue_share_token(db, activity, now)
    activity.status = (
        ActivityStatus.REGISTERING
        if activity.signup_start <= now <= activity.signup_end
        else ActivityStatus.PUBLISHED
    )
    activity.version += 1
    db.add(activity)
    write_log(
        db,
        action="ACTIVITY_PUBLISH",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"checkin_code": code, "share_token": token},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()

    return ok(
        {
            "status": activity.status,
            "checkin_code": code,
            "share_token": token,
            "share_url": f"{api_base(request)}/share/{token}",
        },
        message="活动已发布",
    )


# ---------------------------------------------------------------- A-09 复制
@router.post("/activities/{activity_id}/copy", summary="A-09 复制活动")
def copy_activity(activity_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    """生成草稿，名称追加"（副本）"，不复制报名、签到码与链接。"""
    source = _load_activity(db, admin, activity_id)
    title = source.title if source.title.endswith("（副本）") else f"{source.title}（副本）"
    clone = Activity(
        title=title[:60],
        type_id=source.type_id,
        host=source.host,
        organizer=source.organizer,
        location=source.location,
        cover_url=source.cover_url,
        description=source.description,
        notice=source.notice,
        contact_name=source.contact_name,
        contact_phone=source.contact_phone,
        signup_start=source.signup_start,
        signup_end=source.signup_end,
        start_time=source.start_time,
        end_time=source.end_time,
        capacity=source.capacity,
        need_audit=source.need_audit,
        allow_cancel=source.allow_cancel,
        hours=source.hours,
        checkin_before=source.checkin_before,
        checkin_after=source.checkin_after,
        status=ActivityStatus.DRAFT,
        version=1,
        created_by=admin.id,
    )
    db.add(clone)
    db.flush()
    write_log(
        db,
        action="ACTIVITY_COPY",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=clone.id,
        detail={"source_id": source.id},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(
        {"id": clone.id, "version": clone.version, "status": clone.status}, message="已复制为草稿"
    )


# ---------------------------------------------------------------- A-10 取消
@router.post("/activities/{activity_id}/cancel", summary="A-10 取消活动")
def cancel_activity(
    activity_id: int, payload: ActivityCancelIn, db: DbSession, admin: CurrentAdmin, request: Request
):
    """仅活动开始前可取消；全部有效报名置为 CANCELLED，签到立即失效。"""
    now = now_local()
    activity = _load_activity(db, admin, activity_id, now)

    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消")
    if activity.status == ActivityStatus.FINISHED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已结束，无法取消")
    if activity.start_time and now >= activity.start_time:
        fail(ErrorCode.ILLEGAL_STATE, "活动已开始，无法取消，请使用提前结束")

    affected = db.execute(
        update(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status.in_(
                [
                    RegistrationStatus.PENDING,
                    RegistrationStatus.APPROVED,
                    RegistrationStatus.ABSENT,
                ]
            ),
        )
        .values(
            status=RegistrationStatus.CANCELLED,
            cancel_reason=f"活动取消：{payload.reason}",
        )
        .execution_options(synchronize_session=False)
    ).rowcount

    activity.status = ActivityStatus.CANCELLED
    activity.version += 1
    db.add(activity)
    write_log(
        db,
        action="ACTIVITY_CANCEL",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"reason": payload.reason, "cancelled_registrations": affected},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(
        {"id": activity.id, "status": activity.status, "cancelled_registrations": affected},
        message="活动已取消",
    )


# ---------------------------------------------------------------- A-11 结束
@router.post("/activities/{activity_id}/finish", summary="A-11 提前结束活动")
def finish_activity(activity_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    """状态置 FINISHED，未签到报名置 ABSENT，已签到置 COMPLETED，返回 absent_count。"""
    now = now_local()
    activity = _load_activity(db, admin, activity_id, now)

    if activity.status == ActivityStatus.FINISHED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已结束")
    if activity.status in (ActivityStatus.DRAFT, ActivityStatus.CANCELLED):
        fail(ErrorCode.ILLEGAL_STATE, "该状态的活动无法结束")

    rejected = db.execute(
        update(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status == RegistrationStatus.PENDING,
        )
        .values(
            status=RegistrationStatus.REJECTED,
            audit_at=now,
            audit_remark="活动已开始，未完成审核",
        )
        .execution_options(synchronize_session=False)
    ).rowcount

    absent = db.execute(
        update(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status == RegistrationStatus.APPROVED,
        )
        .values(status=RegistrationStatus.ABSENT)
        .execution_options(synchronize_session=False)
    ).rowcount

    completed = db.execute(
        update(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status == RegistrationStatus.CHECKED_IN,
        )
        .values(status=RegistrationStatus.COMPLETED)
        .execution_options(synchronize_session=False)
    ).rowcount

    activity.status = ActivityStatus.FINISHED
    activity.version += 1
    db.add(activity)
    write_log(
        db,
        action="ACTIVITY_FINISH",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"absent_count": absent, "rejected_count": rejected, "completed_count": completed},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(
        {
            "id": activity.id,
            "status": activity.status,
            "absent_count": absent,
            "rejected_count": rejected,
            "completed_count": completed,
        },
        message="活动已结束",
    )


# ---------------------------------------------------------------- A-12 删除
@router.delete("/activities/{activity_id}", summary="A-12 删除活动")
def delete_activity(
    activity_id: int,
    db: DbSession,
    admin: CurrentAdmin,
    request: Request,
    payload: ActivityReasonIn | None = Body(None),
    reason: str | None = Query(None, description="删除原因（也可放在请求体）"),
):
    """仅草稿或已取消且无签到记录的活动可删除，软删除并记日志。"""
    now = now_local()
    activity = _load_activity(db, admin, activity_id, now)
    final_reason = (payload.reason if payload else None) or reason or "未填写原因"

    if activity.status not in (ActivityStatus.DRAFT, ActivityStatus.CANCELLED):
        fail(ErrorCode.ILLEGAL_STATE, "仅草稿或已取消的活动可以删除")
    checked = int(
        db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.activity_id == activity.id, Registration.checkin_at.is_not(None))
        )
        or 0
    )
    if checked:
        fail(ErrorCode.ILLEGAL_STATE, "该活动已有签到记录，不能删除")

    activity.deleted_at = now
    db.add(activity)
    write_log(
        db,
        action="ACTIVITY_DELETE",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"reason": final_reason},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok({"id": activity.id, "deleted": True}, message="活动已删除")


# ---------------------------------------------------------------- A-13 重置签到码
@router.post("/activities/{activity_id}/reset-code", summary="A-13 重置签到码")
def reset_checkin_code(
    activity_id: int, payload: ResetCodeIn, db: DbSession, admin: CurrentAdmin, request: Request
):
    """旧码立即失效，返回新的 checkin_code 与 reset_at。"""
    now = now_local()
    activity = _load_activity(db, admin, activity_id, now)
    if activity.status == ActivityStatus.DRAFT:
        fail(ErrorCode.ILLEGAL_STATE, "草稿活动尚未生成签到码")
    if activity.status == ActivityStatus.CANCELLED:
        fail(ErrorCode.ILLEGAL_STATE, "活动已取消，签到码已失效")

    code = activity_service.issue_checkin_code(
        db, activity, admin_id=admin.id, reason=payload.reason, now=now
    )
    db.add(activity)
    write_log(
        db,
        action="ACTIVITY_RESET_CODE",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"reason": payload.reason},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(
        {
            "id": activity.id,
            "checkin_code": code,
            "reset_at": to_iso(activity.checkin_code_reset_at),
        },
        message="签到码已重置",
    )
