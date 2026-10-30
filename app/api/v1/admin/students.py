"""管理端学生管理与导入（A-22 ~ A-27）。"""
from __future__ import annotations

from fastapi import APIRouter, File, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import (
    CurrentAdmin,
    DbSession,
    ensure_student_scope,
)
from app.core.enums import AccountStatus, ImportResult, RegistrationStatus
from app.core.errors import ErrorCode, fail
from app.core.files import file_response
from app.core.responses import Pagination, ok, page_data
from app.core.security import hash_password, mask_mobile
from app.core.timeutil import now_local, to_iso
from app.models.registration import Registration
from app.models.student import Student
from app.models.system import ImportBatch
from app.schemas.requests import ImportConfirmIn, StudentPatchIn
from app.services import exporter, importer
from app.services import stats as stats_service
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["管理端-学生"])

COUNTED = (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)


def _student_payload(db: Session, student: Student, aggregates: dict | None = None) -> dict:
    agg = (aggregates or {}).get(student.id, {})
    return {
        "id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "college": student.college,
        "major": student.major,
        "class_name": student.class_name,
        "grade": student.grade,
        "mobile": mask_mobile(student.mobile),
        "status": student.status,
        "is_first_login": student.is_first_login,
        "activity_count": int(agg.get("count", 0)),
        "hours_total": round(float(agg.get("hours", 0)), 1),
        "created_at": to_iso(student.created_at),
        "last_login_at": to_iso(student.last_login_at),
    }


def _aggregate(db: Session, student_ids: list[int]) -> dict[int, dict]:
    if not student_ids:
        return {}
    rows = db.execute(
        select(
            Registration.student_id,
            func.count(func.distinct(Registration.activity_id)),
            func.coalesce(func.sum(Registration.hours_awarded), 0),
        )
        .where(Registration.student_id.in_(student_ids), Registration.status.in_(COUNTED))
        .group_by(Registration.student_id)
    ).all()
    return {sid: {"count": int(cnt), "hours": float(hours or 0)} for sid, cnt, hours in rows}


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
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(Student.student_no.like(like), Student.name.like(like)))
    if college:
        conditions.append(Student.college == college)
    if grade:
        conditions.append(Student.grade == grade)
    if status:
        values = [s.strip().upper() for s in status.split(",") if s.strip()]
        invalid = [s for s in values if s not in set(AccountStatus)]
        if invalid:
            fail(ErrorCode.PARAM_INVALID, f"不支持的账号状态: {invalid[0]}",
                 data={"field": "status", "reason": "取值不合法"})
        conditions.append(Student.status.in_(values))

    # 学院范围管理员仅可见本学院学生
    if admin.data_scope == "COLLEGE" and admin.role != "SUPER_ADMIN":
        conditions.append(Student.college == admin.college)

    total = int(db.scalar(select(func.count()).select_from(Student).where(*conditions)) or 0)
    rows = db.scalars(
        select(Student)
        .where(*conditions)
        .order_by(Student.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    aggregates = _aggregate(db, [s.id for s in rows])
    items = [_student_payload(db, s, aggregates) for s in rows]
    return ok(page_data(items, total, pagination.page, pagination.page_size), message=pagination.message())


# ---------------------------------------------------------------- A-23 导入（预览）
@router.post("/students/import", summary="A-23 上传并解析 Excel（预览）")
async def import_students(db: DbSession, admin: CurrentAdmin, request: Request, file: UploadFile = File(...)):
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
        admin_id=admin.id,
    )
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="STUDENT_IMPORT_PREVIEW",
        target_type="import_batch",
        target_id=batch.id,
        detail={"total": total, "valid": len(valid), "invalid": len(errors)},
        ip=client_ip(request),
    )
    db.commit()

    return ok(
        {
            "batch_id": batch.batch_id,
            "total": total,
            "success": len(valid),
            "failed": len(errors),
            "errors": errors[:200],
            "expires_at": to_iso(batch.expires_at),
        },
        message="解析完成，请确认后写入",
    )


# ---------------------------------------------------------------- A-24 确认写入
@router.post("/students/import/{batch_id}/confirm", summary="A-24 确认写入")
def confirm_import(batch_id: str, payload: ImportConfirmIn, db: DbSession, admin: CurrentAdmin, request: Request):
    """duplicate_strategy：skip 跳过 / update 更新非空字段。"""
    batch = db.scalars(select(ImportBatch).where(ImportBatch.batch_id == batch_id)).first()
    if batch is None:
        fail(ErrorCode.NOT_FOUND, "导入批次不存在")

    default_college = admin.college if admin.data_scope == "COLLEGE" else None
    result = importer.confirm_batch(
        db, batch, duplicate_strategy=payload.duplicate_strategy, default_college=default_college
    )
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="STUDENT_IMPORT_CONFIRM",
        target_type="import_batch",
        target_id=batch.id,
        detail=result,
        ip=client_ip(request),
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
            "batch_id": b.batch_id,
            "filename": b.filename,
            "total": b.total,
            "success": b.success,
            "failed": b.failed,
            "status": b.status,
            "confirmed": b.confirmed,
            "duplicate_strategy": b.duplicate_strategy,
            "errors": (b.errors or [])[:200],
            "created_at": to_iso(b.created_at),
            "expires_at": to_iso(b.expires_at),
        }
        for b in rows
    ]
    return ok(page_data(items, total, pagination.page, pagination.page_size))


# ---------------------------------------------------------------- 初始密码清单（一次性）
@router.get("/students/import/{batch_id}/passwords", summary="下载初始密码清单（仅一次）")
def download_passwords(batch_id: str, db: DbSession, admin: CurrentAdmin, request: Request):
    """初始密码清单仅可下载一次，下载后立即清除。"""
    batch = db.scalars(select(ImportBatch).where(ImportBatch.batch_id == batch_id)).first()
    if batch is None:
        fail(ErrorCode.NOT_FOUND, "导入批次不存在")
    if batch.password_downloaded:
        fail(ErrorCode.ILLEGAL_STATE, "初始密码清单已下载，无法再次获取")
    if not batch.password_list:
        fail(ErrorCode.NOT_FOUND, "该批次没有新生成的初始密码")

    settings.ensure_dirs()
    path = settings.EXPORT_DIR / f"passwords_{batch.batch_id}.xlsx"
    exporter.create_password_list(path, batch.password_list)

    batch.password_downloaded = True
    batch.password_list = []
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="STUDENT_PASSWORD_LIST_DOWNLOAD",
        target_type="import_batch",
        target_id=batch.id,
        ip=client_ip(request),
    )
    db.commit()
    return file_response(path, f"初始密码清单_{batch.created_at.strftime('%Y%m%d')}.xlsx")


# ---------------------------------------------------------------- A-26 停用/启用/重置密码
@router.patch("/students/{student_id}", summary="A-26 停用/启用、重置密码")
def patch_student(student_id: int, payload: StudentPatchIn, db: DbSession, admin: CurrentAdmin, request: Request):
    student = db.get(Student, student_id)
    if student is None:
        fail(ErrorCode.NOT_FOUND, "学生不存在")
    ensure_student_scope(admin, student)

    if payload.status is None and payload.reset_password is None and payload.mobile is None:
        fail(ErrorCode.PARAM_INVALID, "未提供可修改的字段",
             data={"field": "status", "reason": "至少提供 status、reset_password 或 mobile"})

    changes: dict = {}
    if payload.status is not None:
        student.status = payload.status
        changes["status"] = payload.status
        if payload.status == AccountStatus.DISABLED:
            # 停用账号所有会话不再续期（文档 2.2）
            from app.services.auth import revoke_other_sessions

            revoke_other_sessions(db, "student", student.id, keep_session_id=None)
    if payload.mobile is not None:
        student.mobile = payload.mobile
        changes["mobile"] = "updated"
    initial_password = None
    if payload.reset_password:
        initial_password = importer.generate_initial_password()
        student.password_hash = hash_password(initial_password)
        student.is_first_login = True
        changes["reset_password"] = True
        from app.services.auth import revoke_other_sessions

        revoke_other_sessions(db, "student", student.id, keep_session_id=None)

    db.add(student)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="STUDENT_UPDATE",
        target_type="student",
        target_id=student.id,
        detail=changes,
        ip=client_ip(request),
    )
    db.commit()
    result = _student_payload(db, student)
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
    student = db.get(Student, student_id)
    if student is None:
        fail(ErrorCode.NOT_FOUND, "学生不存在")
    ensure_student_scope(admin, student)

    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    items, total = stats_service.student_records(
        db, student_id, page_offset=pagination.offset, limit=pagination.page_size
    )
    return ok(
        {
            **page_data(items, total, pagination.page, pagination.page_size),
            "student": _student_payload(db, student),
        }
    )
