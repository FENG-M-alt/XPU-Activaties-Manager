"""导出与下载（A-21 / A-32）。"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession, authenticate_admin_header, ensure_activity_access
from app.core.enums import OCCUPYING_STATUSES, ExportTaskStatus
from app.core.errors import ErrorCode, fail
from app.core.files import file_response
from app.core.guards import enforce_rate
from app.core.responses import ok
from app.core.security import create_download_token, decode_download_token
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity
from app.models.admin import Admin
from app.models.registration import Registration
from app.models.supplement import ExportTask
from app.services import exporter
from app.services.logs import client_ip, client_ua, write_log

router = APIRouter(tags=["管理端-导出"])


@router.get("/activities/{activity_id}/export/registrations", summary="A-21 导出报名与签到名单")
def export_registrations(activity_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    """同步导出（≤ 1 万行）返回文件流；超出返回 202 与 task_id，随后通过 A-32 下载。"""
    activity = db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        fail(ErrorCode.NOT_FOUND, "活动不存在")
    ensure_activity_access(admin, activity, db)
    enforce_rate("export", str(admin.id), settings.RATE_EXPORT)

    now = now_local()
    registrations = db.scalars(
        select(Registration)
        .where(
            Registration.activity_id == activity.id,
            Registration.status.in_(OCCUPYING_STATUSES),
        )
        .order_by(Registration.signup_at.asc())
    ).all()

    task_id = secrets.token_hex(12)
    task = exporter.create_registration_export(
        db, activity, list(registrations), task_id=task_id, operator_id=admin.id, now=now
    )
    db.add(task)
    write_log(
        db,
        action="EXPORT_REGISTRATIONS",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="activities",
        target_id=activity.id,
        detail={"row_count": len(registrations), "task_id": task_id},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()

    if len(registrations) > settings.EXPORT_SYNC_LIMIT:
        download_token = create_download_token(
            subject=admin.id, account_type="admin", path=f"/exports/{task_id}"
        )
        return JSONResponse(
            status_code=202,
            content=ok(
                {
                    "task_id": task_id,
                    "row_count": len(registrations),
                    "status": ExportTaskStatus.DONE,
                    "download_url": f"{request.base_url}api/v1/admin/exports/{task_id}?token={download_token}",
                    "expires_at": to_iso(task.expires_at),
                },
                message="导出文件较大，已生成异步下载任务",
            ),
        )

    return file_response(Path(task.file_path or ""), task.file_name or "export.xlsx")


@router.get("/exports/{task_id}", summary="A-32 下载异步导出文件")
def download_export(
    task_id: str,
    db: DbSession,
    request: Request,
    token: str | None = Query(None, description="带时效下载令牌（与账号绑定）"),
):
    """文件保留 7 天，过期返回 40400。"""
    if token:
        payload = decode_download_token(token)
        if not payload or payload.get("path") != f"/exports/{task_id}":
            fail(ErrorCode.UNAUTHORIZED, "下载令牌无效或已过期")
        admin = db.get(Admin, int(payload["sub"]))
        if admin is None:
            fail(ErrorCode.UNAUTHORIZED)
    else:
        admin = authenticate_admin_header(db, request.headers.get("authorization"))

    task = db.scalars(select(ExportTask).where(ExportTask.task_id == task_id)).first()
    if task is None:
        fail(ErrorCode.NOT_FOUND, "导出任务不存在")
    if task.created_by is not None and task.created_by != admin.id and not admin.is_super_admin:
        fail(ErrorCode.FORBIDDEN, "无权下载该导出文件")
    if task.expires_at <= now_local():
        fail(ErrorCode.NOT_FOUND, "导出文件已过期，请重新导出")
    if not task.file_path or not Path(task.file_path).is_file():
        fail(ErrorCode.NOT_FOUND, "导出文件不存在")

    task.downloaded_at = now_local()
    db.add(task)
    db.commit()
    return file_response(Path(task.file_path), task.file_name or "export.xlsx")


@router.get("/exports", summary="导出任务列表（扩展）")
def list_exports(db: DbSession, admin: CurrentAdmin):
    rows = db.scalars(
        select(ExportTask)
        .where(ExportTask.created_by == admin.id)
        .order_by(ExportTask.created_at.desc())
        .limit(50)
    ).all()
    items = [
        {
            "task_id": t.task_id,
            "activity_id": t.activity_id,
            "file_name": t.file_name,
            "row_count": t.row_count,
            "status": t.status,
            "created_at": to_iso(t.created_at),
            "expires_at": to_iso(t.expires_at),
            "downloaded_at": to_iso(t.downloaded_at),
            "download_url": f"/api/v1/admin/exports/{t.task_id}",
        }
        for t in rows
    ]
    return ok({"items": items, "total": len(items)})
