"""报名取消与详情（U-12 / U-14）。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import ErrorCode, fail
from app.core.responses import ok
from app.core.timeutil import now_local, to_iso
from app.models.registration import Registration
from app.schemas.serializers import registration_student_detail
from app.services import checkin as checkin_service
from app.services import registration as registration_service
from app.services.activity import sync_activity
from app.services.logs import client_ip, client_ua, write_log

router = APIRouter(tags=["用户端-报名"])


def _load_own_registration(db: DbSession, registration_id: int, user) -> Registration:
    registration = db.get(Registration, registration_id)
    if registration is None:
        fail(ErrorCode.NOT_FOUND, "报名记录不存在")
    if registration.user_id != user.id:
        # 学生端只能访问本人资源（接口文档 2.7）
        fail(ErrorCode.FORBIDDEN, "无权访问该报名记录")
    return registration


@router.delete("/registrations/{registration_id}", summary="U-12 取消报名")
def cancel_registration(registration_id: int, db: DbSession, user: CurrentUser, request: Request):
    """报名截止前可取消待审核或已通过的报名；取消后名额自动释放。"""
    now = now_local()
    registration = _load_own_registration(db, registration_id, user)
    if sync_activity(db, registration.activity, now):
        db.commit()

    registration = registration_service.cancel_registration(
        db, registration, reason="学生主动取消", now=now
    )
    write_log(
        db,
        action="CANCEL_SIGNUP",
        operator_type="STUDENT",
        operator_id=user.id,
        target_type="activities",
        target_id=registration.activity_id,
        detail={"registration_id": registration.id, "reason": registration.cancel_reason},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(
        {
            "registration_id": registration.id,
            "status": registration.status,
            "cancel_reason": registration.cancel_reason,
        },
        message="已取消报名",
    )


@router.get("/registrations/{registration_id}", summary="U-14 报名详情")
def get_registration(registration_id: int, db: DbSession, user: CurrentUser):
    """返回报名编号、审核状态、审核意见、审核时间、签到信息、学时与活动摘要。"""
    registration = _load_own_registration(db, registration_id, user)
    data = registration_student_detail(registration)
    data["checkin_logs"] = [
        {
            "action": log.action,
            "method": log.method,
            "reason": log.reason,
            "created_at": to_iso(log.created_at),
        }
        for log in checkin_service.checkin_logs_of(db, registration.id)
    ]
    return ok(data)
