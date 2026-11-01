"""管理端学生管理与导入（A-22 ~ A-27）。

学生表为 users；累计参与次数与学时由 users + registrations + activities 实时聚合。
"""
from __future__ import annotations

from fastapi import APIRouter, File, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession, ensure_user_scope
from app.core.enums import AccountStatus, AccountType, RegistrationStatus
from app.core.errors import ErrorCode, fail
from app.core.files import file_response
from app.core.responses import Pagination, ok, page_data
from app.core.security import hash_password, mask_mobile
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity
from app.models.import_batch import ImportBatch
from app.models.registration import Registration
from app.models.user import User
from app.schemas.requests import ImportConfirmIn, StudentPatchIn
from app.services import exporter, importer
from app.services import stats as stats_service
from app.services.auth import revoke_other_sessions
from app.services.logs import client_ip, client_ua, write_log

router = APIRouter(tags=["管理端-学生"])

COUNTED = (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)


def _aggregate(db: Session, user_ids: list[int]) -> dict[int, dict]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(
            Registration.user_id,
            func.count(func.distinct(Registration.activity_id)),
            func.coalesce(func.sum(Activity.hours), 0),
            func.max(Registration.checkin_at),
        )
        .join(Activity, Activity.id == Registration.activity_id)
        .where(Registration.user_id.in_(user_ids), Registration.status.in_(COUNTED))
        .group_by(Registration.user_id)
    ).all()
    return {
        uid: {"count": int(cnt), "hours": float(hours or 0), "last_checkin": last}
        for uid, cnt, hours, last in rows
    }


def _user_payload(user: User, aggregates: dict | None = None) -> dict:
    agg = (aggregates or {}).get(user.id, {})
    return {
        "id": user.id,
        "student_no": user.student_no,
        "name": user.name,
        "college": user.college,
        "major": user.major,
        "class_name": user.class_name,
        "grade": user.grade,
        "mobile": mask_mobile(user.mobile),
        "status": user.status,
        "is_first_login": user.is_first_login,
        "import_batch_id": user.import_batch_id,
        "activity_count": int(agg.get("count", 0)),
        "hours_total": round(float(agg.get("hours", 0)), 1),
        "last_checkin_at": to_iso(agg.get("last_checkin")),
        "created_at": to_iso(user.created_at),
        "last_login_at": to_iso(user.last_login_at),
    }


# ---------------------------------------------------------------- A-22 列表
@router.get("/students", summary="A-22 学生列表")
def list_students(
    db: DbSession,
    admin: CurrentAdmin,
    keyword: str | None = Query(None, description="学号或姓名"),
    college: str | None = Query(None),
    grade: str | None = Query(None),
    status: str | None = Query(None, description="ACTIVE / DISABLED"),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """支持关键字、学院、年级、状态与分页；手机号脱敏输出。"""
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    conditions = [User.deleted_at.is_(None)]
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(User.student_no.like(like), User.name.like(like)))
    if college:
        conditions.append(User.college == college)
    if grade:
        conditions.append(User.grade == grade)
    if status:
        values = [s.strip().upper() for s in status.split(",") if s.strip()]
        invalid = [s for s in values if s not in set(AccountStatus)]
        if invalid:
            fail(
                ErrorCode.PARAM_INVALID,
                f"不支持的账号状态: {invalid[0]}",
                data={"field": "status", "reason": "取值不合法"},
            )
        conditions.append(User.status.in_(values))

    # 学院范围管理员仅可见本学院学生
    if admin.data_scope == "COLLEGE" and admin.role != "SUPER_ADMIN":
        conditions.append(User.college == admin.college)

    total = int(db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0)
    rows = db.scalars(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    aggregates = _aggregate(db, [u.id for u in rows])
    items = [_user_payload(u, aggregates) for u in rows]
    return ok(
        page_data(items, total, pagination.page, pagination.page_size), message=pagination.message()
    )


# ---------------------------------------------------------------- A-23 导入（预览）
@router.post("/students/import", summary="A-23 上传并解析 Excel（预览）")
async def import_students(
    db: DbSession, admin: CurrentAdmin, request: Request, file: UploadFile = File(...)
):
    """返回 batch_id、total、success、failed、errors；解析结果保留 30 分钟待确认。"""
    content = await file.read()
    importer.validate_upload(file.filename, content)
    valid, errors, total = importer.parse_workbook(content)

    default_college = admin.college if admin.data_scope == "COLLEGE" else None
    if default_college:
        for row in valid:
            row["college"] = row.get("college") or default_college

    batch = importer.create_batch(
        db,
        filename=file.filename or "students.xlsx",
        valid=valid,
        errors=errors,
        total=total,
        operator_id=admin.id,
    )
    write_log(
        db,
        action="STUDENT_IMPORT_PREVIEW",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="import_batches",
        target_id=batch.id,
        detail={"total": total, "valid": len(valid), "invalid": len(errors)},
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()

    return ok(
        {
            "batch_id": batch.id,
            "total": total,
            "success": len(valid),
            "failed": len(errors),
            "errors": errors[:200],
            "error_file_url": batch.error_file_url,
        },
        message="解析完成，请确认后写入",
    )


# ---------------------------------------------------------------- A-24 确认写入
@router.post("/students/import/{batch_id}/confirm", summary="A-24 确认写入")
def confirm_import(
    batch_id: int, payload: ImportConfirmIn, db: DbSession, admin: CurrentAdmin, request: Request
):
    """duplicate_strategy：skip 跳过 / update 更新非空字段。"""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        fail(ErrorCode.NOT_FOUND, "导入批次不存在")

    default_college = admin.college if admin.data_scope == "COLLEGE" else None
    result = importer.confirm_batch(
        db, batch, duplicate_strategy=payload.duplicate_strategy, default_college=default_college
    )
    write_log(
        db,
        action="STUDENT_IMPORT_CONFIRM",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="import_batches",
        target_id=batch.id,
        detail=result,
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    return ok(result, message="导入已完成")


# ---------------------------------------------------------------- A-25 批次列表
@router.get("/students/import/batches", summary="A-25 导入批次与失败明细")
def list_import_batches(
    db: DbSession,
    admin: CurrentAdmin,
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    total = int(db.scalar(select(func.count()).select_from(ImportBatch)) or 0)
    rows = db.scalars(
        select(ImportBatch)
        .order_by(ImportBatch.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    items = [
        {
            "batch_id": b.id,
            "file_name": b.file_name,
            "operator_id": b.operator_id,
            "total": b.total,
            "success": b.success,
            "failed": b.failed,
            "status": b.status,
            "error_file_url": b.error_file_url,
            "created_at": to_iso(b.created_at),
        }
        for b in rows
    ]
    return ok(page_data(items, total, pagination.page, pagination.page_size))


# ---------------------------------------------------------------- 初始密码清单（一次性）
@router.get("/students/import/{batch_id}/passwords", summary="下载初始密码清单（仅一次）")
def download_passwords(batch_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    """初始密码清单存于文件，下载一次后即删除，无法再次获取。"""
    from app.core.errors import ErrorCode as EC

    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        fail(EC.NOT_FOUND, "导入批次不存在")
    path = importer.password_file_path(batch_id)
    if not path.is_file():
        fail(EC.ILLEGAL_STATE, "初始密码清单已下载或不存在，无法再次获取")

    write_log(
        db,
        action="STUDENT_PASSWORD_LIST_DOWNLOAD",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="import_batches",
        target_id=batch.id,
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()

    response = file_response(path, f"初始密码清单_{batch.created_at.strftime('%Y%m%d')}.xlsx")
    # 一次性：发送后删除文件（Starlette 在返回时读取，故用后台清理）
    from starlette.background import BackgroundTask

    response.background = BackgroundTask(lambda: path.unlink(missing_ok=True))
    return response


@router.get("/students/import/{batch_id}/errors", summary="下载导入失败明细")
def download_errors(batch_id: int, db: DbSession, admin: CurrentAdmin):
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        fail(ErrorCode.NOT_FOUND, "导入批次不存在")
    path = importer.error_file_path(batch_id)
    if not path.is_file():
        fail(ErrorCode.NOT_FOUND, "该批次没有失败明细文件")
    return file_response(path, f"导入失败明细_{batch_id}.xlsx")


# ---------------------------------------------------------------- A-26 停用/启用/重置密码
@router.patch("/students/{student_id}", summary="A-26 停用/启用、重置密码")
def patch_student(
    student_id: int, payload: StudentPatchIn, db: DbSession, admin: CurrentAdmin, request: Request
):
    user = db.get(User, student_id)
    if user is None or user.deleted_at is not None:
        fail(ErrorCode.NOT_FOUND, "学生不存在")
    ensure_user_scope(admin, user)

    if payload.status is None and payload.reset_password is None and payload.mobile is None:
        fail(
            ErrorCode.PARAM_INVALID,
            "未提供可修改的字段",
            data={"field": "status", "reason": "至少提供 status、reset_password 或 mobile"},
        )

    changes: dict = {}
    if payload.status is not None:
        user.status = payload.status
        changes["status"] = payload.status
        if payload.status == AccountStatus.DISABLED:
            # 停用账号所有会话不再续期（接口文档 2.2）
            revoke_other_sessions(db, AccountType.USER, user.id, keep_session_id=None)
    if payload.mobile is not None:
        user.mobile = payload.mobile
        changes["mobile"] = "updated"

    initial_password = None
    if payload.reset_password:
        initial_password = importer.generate_initial_password()
        user.password_hash = hash_password(initial_password)
        user.is_first_login = True
        changes["reset_password"] = True
        revoke_other_sessions(db, AccountType.USER, user.id, keep_session_id=None)

    db.add(user)
    write_log(
        db,
        action="STUDENT_UPDATE",
        operator_type="ADMIN",
        operator_id=admin.id,
        target_type="users",
        target_id=user.id,
        detail=changes,
        ip=client_ip(request),
        ua=client_ua(request),
    )
    db.commit()
    result = _user_payload(user, _aggregate(db, [user.id]))
    if initial_password:
        result["initial_password"] = initial_password
        result["notice"] = "请将初始密码线下告知学生，学生首次登录后须修改密码"
    return ok(result, message="保存成功")


# ---------------------------------------------------------------- A-27 参与记录
@router.get("/students/{student_id}/records", summary="A-27 学生参与记录")
def student_records(
    student_id: int,
    db: DbSession,
    admin: CurrentAdmin,
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    user = db.get(User, student_id)
    if user is None or user.deleted_at is not None:
        fail(ErrorCode.NOT_FOUND, "学生不存在")
    ensure_user_scope(admin, user)

    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    items, total = stats_service.student_records(
        db, student_id, page_offset=pagination.offset, limit=pagination.page_size
    )
    return ok(
        {
            **page_data(items, total, pagination.page, pagination.page_size),
            "student": _user_payload(user, _aggregate(db, [user.id])),
        }
    )
