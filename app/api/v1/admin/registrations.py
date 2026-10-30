"""管理端报名名单与审核（A-14 ~ A-20）。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession, SuperAdmin, ensure_activity_access
from app.core.enums import OCCUPYING_STATUSES, ActivityStatus, RegistrationStatus
from app.core.errors import BizError, ErrorCode, fail
from app.core.responses import Pagination, ok, page_data
from app.core.timeutil import now_local
from app.models.activity import Activity
from app.models.registration import Registration
from app.models.student import Student
from app.schemas.requests import AuditIn, BatchAuditIn, ManualCheckinIn
from app.schemas.serializers import registration_admin_item
from app.services import checkin as checkin_service
from app.services import registration as registration_service
from app.services import stats as stats_service
from app.services.activity import sync_activity
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["管理端-报名"])

APPROVED_STATUSES = (
    RegistrationStatus.APPROVED,
    RegistrationStatus.CHECKED_IN,
    RegistrationStatus.COMPLETED,
    RegistrationStatus.ABSENT,
)


def _load_activity(db: DbSession, admin, activity_id: int) -> Activity:
    activity = db.get(Activity, activity_id)
    if activity is None or activity.is_deleted:
        fail(ErrorCode.NOT_FOUND, "活动不存在")
    ensure_activity_access(admin, activity)
    if sync_activity(db, activity, now_local()):
        db.commit()
        db.refresh(activity)
    return activity


def _load_registration(db: DbSession, admin, registration_id: int) -> Registration:
    registration = db.get(Registration, registration_id)
    if registration is None:
        fail(ErrorCode.NOT_FOUND, "报名记录不存在")
    ensure_activity_access(admin, registration.activity)
    return registration


def remaining_for_approval(db: DbSession, activity: Activity) -> int:
    approved = int(
        db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.activity_id == activity.id, Registration.status.in_(APPROVED_STATUSES))
        )
        or 0
    )
    return max(activity.capacity - approved, 0)


# ---------------------------------------------------------------- A-14 名单
@router.get("/activities/{activity_id}/registrations", summary="A-14 报名名单")
def list_registrations(
    activity_id: int,
    db: DbSession,
    admin: CurrentAdmin,
    keyword: str | None = Query(None, description="姓名或学号"),
    status: str | None = Query(None, description="报名状态"),
    sort: str | None = Query("signup_at", description="signup_at（默认倒序）"),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """每项含学号、姓名、学院、班级、报名与签到信息。"""
    activity = _load_activity(db, admin, activity_id)
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)

    stmt = (
        select(Registration)
        .join(Student, Student.id == Registration.student_id)
        .where(Registration.activity_id == activity.id)
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Student.name.like(like) | Student.student_no.like(like))
    if status:
        values = [s.strip().upper() for s in status.split(",") if s.strip()]
        invalid = [s for s in values if s not in set(RegistrationStatus)]
        if invalid:
            fail(ErrorCode.PARAM_INVALID, f"不支持的报名状态: {invalid[0]}",
                 data={"field": "status", "reason": "取值不合法"})
        stmt = stmt.where(Registration.status.in_(values))

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    order_column = Registration.checkin_at if sort == "checkin_at" else Registration.signup_at
    rows = db.scalars(
        stmt.order_by(order_column.desc()).offset(pagination.offset).limit(pagination.page_size)
    ).all()

    items = [registration_admin_item(r) for r in rows]
    return ok(
        page_data(items, total, pagination.page, pagination.page_size),
        message=pagination.message(),
    )


# ---------------------------------------------------------------- A-15 审核通过
@router.post("/registrations/{registration_id}/approve", summary="A-15 审核通过")
def approve(registration_id: int, db: DbSession, admin: CurrentAdmin, request: Request, payload: AuditIn | None = None):
    """校验名额上限，超限返回 40901。"""
    registration = _load_registration(db, admin, registration_id)
    activity = registration.activity
    if remaining_for_approval(db, activity) <= 0:
        fail(ErrorCode.CAPACITY_FULL, "名额不足，无法通过审核")

    registration = registration_service.approve_registration(
        db, registration, admin_id=admin.id, remark=(payload.reason if payload else None)
    )
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="REGISTRATION_APPROVE",
        target_type="registration",
        target_id=registration.id,
        detail={"activity_id": activity.id},
        ip=client_ip(request),
    )
    db.commit()
    return ok(
        {"registration_id": registration.id, "status": registration.status},
        message="已通过审核",
    )


# ---------------------------------------------------------------- A-16 审核拒绝
@router.post("/registrations/{registration_id}/reject", summary="A-16 审核拒绝")
def reject(registration_id: int, payload: AuditIn, db: DbSession, admin: CurrentAdmin, request: Request):
    """拒绝必须填写理由，理由在学生的报名详情中可见。"""
    if not payload.reason or not payload.reason.strip():
        fail(ErrorCode.PARAM_INVALID, "拒绝时必须填写理由",
             data={"field": "reason", "reason": "必填"})
    registration = _load_registration(db, admin, registration_id)
    registration = registration_service.reject_registration(
        db, registration, admin_id=admin.id, reason=payload.reason.strip()
    )
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="REGISTRATION_REJECT",
        target_type="registration",
        target_id=registration.id,
        detail={"activity_id": registration.activity_id, "reason": payload.reason},
        ip=client_ip(request),
    )
    db.commit()
    return ok({"registration_id": registration.id, "status": registration.status}, message="已拒绝")


# ---------------------------------------------------------------- A-17 批量审核
@router.post("/activities/{activity_id}/batch-audit", summary="A-17 批量审核")
def batch_audit(activity_id: int, payload: BatchAuditIn, db: DbSession, admin: CurrentAdmin, request: Request):
    """最多 200 条；部分失败不影响其他记录。"""
    activity = _load_activity(db, admin, activity_id)
    if payload.action == "reject" and (not payload.reason or not payload.reason.strip()):
        fail(ErrorCode.PARAM_INVALID, "批量拒绝时必须填写理由",
             data={"field": "reason", "reason": "必填"})

    success = 0
    failed: list[dict] = []
    slots = remaining_for_approval(db, activity) if payload.action == "approve" else len(payload.ids)

    for registration_id in payload.ids:
        try:
            with db.begin_nested():
                registration = db.get(Registration, registration_id)
                if registration is None:
                    raise BizError(ErrorCode.NOT_FOUND, "报名记录不存在")
                if registration.activity_id != activity.id:
                    raise BizError(ErrorCode.PARAM_INVALID, "报名记录不属于该活动")
                if payload.action == "approve":
                    nonlocal_slots = slots
                    if nonlocal_slots <= 0:
                        raise BizError(ErrorCode.CAPACITY_FULL, "名额不足")
                    registration_service.approve_registration(
                        db, registration, admin_id=admin.id, remark=payload.reason
                    )
                    slots -= 1
                else:
                    registration_service.reject_registration(
                        db, registration, admin_id=admin.id, reason=payload.reason.strip()  # type: ignore[union-attr]
                    )
            success += 1
        except BizError as exc:
            failed.append({"id": registration_id, "code": exc.code, "message": exc.message})
        except Exception as exc:  # pragma: no cover - 兜底
            failed.append({"id": registration_id, "code": ErrorCode.INTERNAL_ERROR, "message": str(exc)})

    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="REGISTRATION_BATCH_AUDIT",
        target_type="activity",
        target_id=activity.id,
        detail={"action": payload.action, "success": success, "failed": len(failed)},
        ip=client_ip(request),
    )
    db.commit()
    return ok({"success_count": success, "failed": failed}, message="批量审核完成")


# ---------------------------------------------------------------- A-18 补签
@router.post("/registrations/{registration_id}/checkin", summary="A-18 管理员补签")
def manual_checkin(registration_id: int, payload: ManualCheckinIn, db: DbSession, admin: CurrentAdmin, request: Request):
    """签到方式记为 MANUAL 并记录操作人；超过补签期限需超管。"""
    registration = _load_registration(db, admin, registration_id)
    registration = checkin_service.manual_checkin(
        db,
        registration,
        admin_id=admin.id,
        admin_name=admin.name,
        reason=payload.reason,
        checkin_at=payload.checkin_at,
        is_super_admin=admin.is_super_admin,
        ip=client_ip(request),
    )
    db.add(registration)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="REGISTRATION_CHECKIN",
        target_type="registration",
        target_id=registration.id,
        detail={"reason": payload.reason, "method": "MANUAL"},
        ip=client_ip(request),
    )
    db.commit()
    db.refresh(registration)
    return ok(
        {
            "registration_id": registration.id,
            "status": registration.status,
            "checkin_at": registration.checkin_at.isoformat() + "+08:00" if registration.checkin_at else None,
            "checkin_method": registration.checkin_method,
            "is_late": registration.is_late,
            "hours": float(registration.hours_awarded or 0),
        },
        message="补签成功",
    )


# ---------------------------------------------------------------- A-19 撤销签到
@router.delete("/registrations/{registration_id}/checkin", summary="A-19 撤销签到（超管）")
def undo_checkin(registration_id: int, payload: AuditIn, db: DbSession, admin: SuperAdmin, request: Request):
    """撤销后报名回到 APPROVED 并扣减学时。"""
    if not payload.reason or not payload.reason.strip():
        fail(ErrorCode.PARAM_INVALID, "撤销签到必须填写原因",
             data={"field": "reason", "reason": "必填"})
    registration = _load_registration(db, admin, registration_id)
    registration = checkin_service.undo_checkin(
        db, registration, admin_id=admin.id, reason=payload.reason.strip()
    )
    db.add(registration)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="REGISTRATION_UNDO_CHECKIN",
        target_type="registration",
        target_id=registration.id,
        detail={"reason": payload.reason},
        ip=client_ip(request),
    )
    db.commit()
    return ok(
        {"registration_id": registration.id, "status": registration.status},
        message="已撤销签到",
    )


# ---------------------------------------------------------------- A-20 签到看板
@router.get("/activities/{activity_id}/checkin-board", summary="A-20 签到看板")
def checkin_board(activity_id: int, db: DbSession, admin: CurrentAdmin):
    """轮询接口，服务端不做推送；学号脱敏展示。"""
    activity = _load_activity(db, admin, activity_id)
    return ok(stats_service.checkin_board(db, activity))
