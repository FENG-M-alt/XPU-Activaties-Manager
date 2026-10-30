"""字典、系统参数、操作日志与管理员账号（A-33 ~ A-36）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession, SuperAdmin
from app.core.enums import AccountStatus, AdminRole, DataScope
from app.core.errors import ErrorCode, fail
from app.core.files import file_response
from app.core.responses import Pagination, ok, page_data
from app.core.security import hash_password, password_strength_error
from app.core.timeutil import day_end, day_start, now_local, to_iso
from app.models.audit import OperationLog
from app.models.system import DictItem, SystemSetting
from app.models.admin import Admin
from app.schemas.requests import (
    AdminCreateIn,
    AdminPatchIn,
    DictItemIn,
    DictItemPatchIn,
    SettingsUpdateIn,
)
from app.services import auth as auth_service
from app.services import exporter
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["管理端-系统"])


# ---------------------------------------------------------------- A-33 字典
@router.get("/dicts", summary="A-33 字典查询")
def list_dicts(db: DbSession, admin: CurrentAdmin, dict_type: str | None = Query(None)):
    """按 dict_type 查询字典项；不传时返回全部类型。"""
    stmt = select(DictItem).where(DictItem.is_active.is_(True))
    if dict_type:
        stmt = stmt.where(DictItem.dict_type == dict_type)
    rows = db.scalars(stmt.order_by(DictItem.dict_type.asc(), DictItem.sort.asc())).all()

    grouped: dict[str, list[dict]] = {}
    for item in rows:
        grouped.setdefault(item.dict_type, []).append(
            {"id": item.id, "label": item.label, "value": item.value, "sort": item.sort, "remark": item.remark}
        )
    if dict_type:
        return ok({"dict_type": dict_type, "items": grouped.get(dict_type, [])})
    return ok({"items": grouped})


@router.post("/dicts", summary="A-33 新增字典项（超管）")
def create_dict(payload: DictItemIn, db: DbSession, admin: SuperAdmin, request: Request):
    exists = db.scalars(
        select(DictItem).where(
            DictItem.dict_type == payload.dict_type,
            DictItem.value == payload.value,
        )
    ).first()
    if exists:
        fail(ErrorCode.ILLEGAL_STATE, "同类型下字典值已存在")
    item = DictItem(**payload.model_dump())
    db.add(item)
    db.flush()
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="DICT_CREATE",
        target_type="dict",
        target_id=item.id,
        detail=payload.model_dump(),
        ip=client_ip(request),
    )
    db.commit()
    return ok({"id": item.id}, message="已创建")


@router.patch("/dicts/{dict_id}", summary="A-33 修改字典项（超管）")
def update_dict(dict_id: int, payload: DictItemPatchIn, db: DbSession, admin: SuperAdmin, request: Request):
    item = db.get(DictItem, dict_id)
    if item is None:
        fail(ErrorCode.NOT_FOUND, "字典项不存在")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(item, field, value)
    db.add(item)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="DICT_UPDATE",
        target_type="dict",
        target_id=item.id,
        detail=data,
        ip=client_ip(request),
    )
    db.commit()
    return ok({"id": item.id}, message="已保存")


@router.delete("/dicts/{dict_id}", summary="A-33 删除字典项（超管）")
def delete_dict(dict_id: int, db: DbSession, admin: SuperAdmin, request: Request):
    item = db.get(DictItem, dict_id)
    if item is None:
        fail(ErrorCode.NOT_FOUND, "字典项不存在")
    item.is_active = False
    db.add(item)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="DICT_DELETE",
        target_type="dict",
        target_id=item.id,
        ip=client_ip(request),
    )
    db.commit()
    return ok({"id": dict_id}, message="已删除")


# ---------------------------------------------------------------- A-34 系统参数
@router.get("/settings", summary="A-34 系统参数查询")
def list_settings(db: DbSession, admin: CurrentAdmin):
    """返回参数键值、默认值与说明。"""
    rows = db.scalars(select(SystemSetting).order_by(SystemSetting.group.asc(), SystemSetting.key.asc())).all()
    items = [
        {
            "key": s.key,
            "value": s.value if s.value is not None else s.default_value,
            "default_value": s.default_value,
            "description": s.description,
            "group": s.group,
            "updated_at": to_iso(s.updated_at),
        }
        for s in rows
    ]
    return ok({"items": items, "total": len(items)})


@router.patch("/settings", summary="A-34 更新系统参数（超管）")
def update_settings(payload: SettingsUpdateIn, db: DbSession, admin: SuperAdmin, request: Request):
    """记录修改人与前后值。"""
    changes = []
    for item in payload.items:
        setting = db.get(SystemSetting, item.key)
        if setting is None:
            fail(ErrorCode.NOT_FOUND, f"参数 {item.key} 不存在")
        before = setting.value
        setting.value = item.value
        setting.updated_by = admin.id
        setting.updated_at = now_local()
        db.add(setting)
        changes.append({"key": item.key, "before": before, "after": item.value})

    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="SETTING_UPDATE",
        target_type="setting",
        detail={"changes": changes},
        ip=client_ip(request),
    )
    db.commit()
    return ok({"updated": len(changes), "changes": changes}, message="已保存")


# ---------------------------------------------------------------- A-35 操作日志
@router.get("/logs", summary="A-35 操作日志查询")
def list_logs(
    db: DbSession,
    admin: CurrentAdmin,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    actor: str | None = Query(None, description="操作人姓名或账号"),
    action: str | None = Query(None, description="动作关键字"),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """管理员仅能查本人日志，超管可查全部。"""
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    conditions = []
    if admin.role != AdminRole.SUPER_ADMIN:
        conditions.append(OperationLog.actor_type == "admin")
        conditions.append(OperationLog.actor_id == admin.id)
    if start_date:
        conditions.append(OperationLog.created_at >= day_start(start_date))
    if end_date:
        conditions.append(OperationLog.created_at <= day_end(end_date))
    if actor:
        like = f"%{actor.strip()}%"
        conditions.append(OperationLog.actor_name.like(like))
    if action:
        conditions.append(OperationLog.action.like(f"%{action.strip().upper()}%"))

    total = int(db.scalar(select(func.count()).select_from(OperationLog).where(*conditions)) or 0)
    rows = db.scalars(
        select(OperationLog)
        .where(*conditions)
        .order_by(OperationLog.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    items = [
        {
            "id": log.id,
            "actor_type": log.actor_type,
            "actor_id": log.actor_id,
            "actor_name": log.actor_name,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": log.detail,
            "ip": log.ip,
            "created_at": to_iso(log.created_at),
        }
        for log in rows
    ]
    return ok(page_data(items, total, pagination.page, pagination.page_size))


@router.get("/logs/export", summary="A-35 导出操作日志")
def export_logs(db: DbSession, admin: CurrentAdmin, request: Request):
    conditions = []
    if admin.role != AdminRole.SUPER_ADMIN:
        conditions = [OperationLog.actor_type == "admin", OperationLog.actor_id == admin.id]
    rows = db.scalars(
        select(OperationLog).where(*conditions).order_by(OperationLog.created_at.desc()).limit(20000)
    ).all()

    settings.ensure_dirs()
    name = f"操作日志_{now_local().strftime('%Y%m%d%H%M')}.xlsx"
    path = settings.EXPORT_DIR / f"logs_{now_local().strftime('%Y%m%d%H%M%S')}.xlsx"
    exporter.create_generic_export(
        path,
        ["时间", "操作人类型", "操作人", "动作", "对象类型", "对象ID", "IP", "详情"],
        [
            [
                to_iso(log.created_at),
                log.actor_type,
                log.actor_name or "",
                log.action,
                log.target_type or "",
                log.target_id or "",
                log.ip or "",
                log.detail or "",
            ]
            for log in rows
        ],
        "操作日志",
    )
    return file_response(path, name)


# ---------------------------------------------------------------- A-36 管理员账号
@router.get("/admins", summary="A-36 管理员列表（超管）")
def list_admins(db: DbSession, admin: SuperAdmin):
    rows = db.scalars(select(Admin).order_by(Admin.created_at.asc())).all()
    items = [
        {
            "id": a.id,
            "username": a.username,
            "name": a.name,
            "role": a.role,
            "data_scope": a.data_scope,
            "college": a.college,
            "status": a.status,
            "last_login_at": to_iso(a.last_login_at),
            "created_at": to_iso(a.created_at),
        }
        for a in rows
    ]
    return ok({"items": items, "total": len(items)})


@router.post("/admins", summary="A-36 创建管理员（超管）")
def create_admin(payload: AdminCreateIn, db: DbSession, admin: SuperAdmin, request: Request):
    if db.scalars(select(Admin).where(Admin.username == payload.username)).first():
        fail(ErrorCode.ILLEGAL_STATE, "账号已存在")
    reason = password_strength_error(payload.password)
    if reason:
        fail(ErrorCode.PARAM_INVALID, reason, data={"field": "password", "reason": reason})
    if payload.data_scope == DataScope.COLLEGE and not payload.college:
        fail(ErrorCode.PARAM_INVALID, "数据范围为指定学院时必须填写学院",
             data={"field": "college", "reason": "必填"})

    created = Admin(
        username=payload.username,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        data_scope=payload.data_scope,
        college=payload.college,
        status=payload.status,
        is_first_login=True,
    )
    db.add(created)
    db.flush()
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="ADMIN_CREATE",
        target_type="admin",
        target_id=created.id,
        detail={"username": created.username, "role": created.role},
        ip=client_ip(request),
    )
    db.commit()
    return ok({"id": created.id, "username": created.username}, message="管理员已创建")


@router.patch("/admins/{admin_id}", summary="A-36 编辑管理员（超管）")
def update_admin(admin_id: int, payload: AdminPatchIn, db: DbSession, admin: SuperAdmin, request: Request):
    target = db.get(Admin, admin_id)
    if target is None:
        fail(ErrorCode.NOT_FOUND, "管理员不存在")

    data = payload.model_dump(exclude_unset=True)
    if target.id == admin.id and data.get("status") == AccountStatus.DISABLED:
        fail(ErrorCode.ILLEGAL_STATE, "不能停用当前登录账号")
    if target.id == admin.id and data.get("role") == AdminRole.ADMIN:
        fail(ErrorCode.ILLEGAL_STATE, "不能降级当前登录账号")

    if "password" in data and data["password"]:
        reason = password_strength_error(data["password"])
        if reason:
            fail(ErrorCode.PARAM_INVALID, reason, data={"field": "password", "reason": reason})
        target.password_hash = hash_password(data.pop("password"))
        target.is_first_login = True
        auth_service.revoke_other_sessions(db, "admin", target.id, keep_session_id=None)

    scope = data.get("data_scope", target.data_scope)
    college = data.get("college", target.college)
    if scope == DataScope.COLLEGE and not college:
        fail(ErrorCode.PARAM_INVALID, "数据范围为指定学院时必须填写学院",
             data={"field": "college", "reason": "必填"})

    for field, value in data.items():
        setattr(target, field, value)
    if data.get("status") == AccountStatus.DISABLED:
        auth_service.revoke_other_sessions(db, "admin", target.id, keep_session_id=None)

    db.add(target)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="ADMIN_UPDATE",
        target_type="admin",
        target_id=target.id,
        detail={"fields": list(data.keys())},
        ip=client_ip(request),
    )
    db.commit()
    return ok({"id": target.id}, message="已保存")
