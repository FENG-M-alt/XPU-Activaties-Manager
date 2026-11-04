"""个人中心路由（U-05 / U-06 / U-13 / U-16）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.config import settings
from app.core.deps import CurrentUser, DbSession, require_password_change_done
from app.core.enums import FINISHED_GROUP_STATUSES, RegistrationStatus
from app.core.errors import ErrorCode, fail
from app.core.responses import Pagination, ok, page_data
from app.core.timeutil import now_local, term_bounds, to_iso
from app.models.registration import Registration
from app.schemas.requests import UpdateMeIn
from app.schemas.serializers import registration_student_item, student_me
from app.services.logs import client_ip, client_ua, write_log
from app.services.stats import student_stats

router = APIRouter(tags=["个人中心"])


@router.get("/me", summary="U-05 个人信息")
def get_me(user: CurrentUser, db: DbSession):
    """返回学号、姓名、学院、专业、班级、年级、手机号、is_first_login、账号状态。"""
    summary = student_stats(db, user.id)["summary"]
    return ok(student_me(user, summary))


@router.patch("/me", summary="U-06 修改个人信息", dependencies=[Depends(require_password_change_done)])
def update_me(payload: UpdateMeIn, user: CurrentUser, request: Request, db: DbSession):
    """仅允许修改手机号；姓名、学号、学院等字段提交即返回 40001。"""
    user.mobile = payload.mobile
    db.add(user)
    write_log(
        db,
        action="UPDATE_PROFILE",
        operator_type="STUDENT",
        operator_id=user.id,
        target_type="users",
        target_id=user.id,
        detail={"field": "mobile"},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    db.refresh(user)
    return ok(student_me(user, student_stats(db, user.id)["summary"]), message="保存成功")


@router.get("/me/registrations", summary="U-13 我的报名", dependencies=[Depends(require_password_change_done)])
def my_registrations(
    user: CurrentUser,
    db: DbSession,
    status: str = Query("ALL", description="ALL / PENDING / APPROVED / FINISHED / CANCELLED"),
    term: str | None = Query(None, description="学期，默认当前学期；传 all 表示全部"),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """按状态与学期筛选我的报名；FINISHED 包含已签到、已完成、缺席三种状态。"""
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_STUDENT)
    normalized = (status or "ALL").upper()
    allowed = {
        "ALL",
        "PENDING",
        "APPROVED",
        "FINISHED",
        "CANCELLED",
        "REJECTED",
        "ABSENT",
        "CHECKED_IN",
    }
    if normalized not in allowed:
        fail(
            ErrorCode.PARAM_INVALID,
            "status 取值不合法",
            data={"field": "status", "reason": "取值不合法"},
        )

    stmt = select(Registration).where(Registration.user_id == user.id)

    if normalized == "FINISHED":
        stmt = stmt.where(Registration.status.in_(FINISHED_GROUP_STATUSES))
    elif normalized == "CANCELLED":
        stmt = stmt.where(
            Registration.status.in_([RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED])
        )
    elif normalized != "ALL":
        stmt = stmt.where(Registration.status == normalized)

    if term and term.lower() != "all":
        start, end = term_bounds(now_local())
        stmt = stmt.where(Registration.signup_at >= start, Registration.signup_at <= end)

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.scalars(
        stmt.order_by(Registration.signup_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    items = [registration_student_item(r) for r in rows]
    return ok(
        page_data(items, total, pagination.page, pagination.page_size),
        message=pagination.message(),
    )


@router.get("/me/stats", summary="U-16 我的统计", dependencies=[Depends(require_password_change_done)])
def my_stats(user: CurrentUser, db: DbSession):
    """个人统计概览、类型分布、近 6 个月趋势与活动明细（接口文档 4.10 口径）。"""
    data = student_stats(db, user.id)
    data["generated_at"] = to_iso(now_local())
    return ok(data)
