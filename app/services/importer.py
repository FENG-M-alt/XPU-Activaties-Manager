"""学生 Excel 导入：解析预览 + 确认写入（接口文档 A-23 ~ A-25）。

对齐数据库设计：
- 批次记录写入 import_batches(file_name, operator_id, total, success, failed, status, error_file_url)；
- 失败明细写文件，地址存 error_file_url，不落库；
- 待确认的解析结果放进程内 TTL 缓存（IMPORT_BATCH_TTL，默认 30 分钟），与"解析结果保留 30 分钟"一致；
- 新建学生写入 users，并记录 import_batch_id 便于批次追溯与整批撤销。
"""
from __future__ import annotations

import io
import re
import secrets
import string
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import cache
from app.core.enums import AccountStatus, ImportResult
from app.core.errors import ErrorCode, fail
from app.core.security import hash_password
from app.core.timeutil import now_local
from app.models.import_batch import ImportBatch
from app.models.user import User
from app.services import exporter

REQUIRED_HEADERS = {
    "学号": "student_no",
    "姓名": "name",
    "学院": "college",
    "专业": "major",
    "班级": "class_name",
    "年级": "grade",
    "手机号": "mobile",
}

HEADER_ALIASES = {
    "student_no": "student_no",
    "name": "name",
    "college": "college",
    "major": "major",
    "class_name": "class_name",
    "class": "class_name",
    "grade": "grade",
    "mobile": "mobile",
    "phone": "mobile",
}

STUDENT_NO_RE = re.compile(r"^[A-Za-z0-9]{6,20}$")
MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
XLSX_MAGIC = b"PK\x03\x04"


def _preview_key(batch_id: int) -> str:
    return f"import_preview:{batch_id}"


def validate_upload(filename: str | None, content: bytes) -> None:
    if not filename or not filename.lower().endswith(".xlsx"):
        fail(
            ErrorCode.PARAM_INVALID,
            "仅支持 .xlsx 文件",
            data={"field": "file", "reason": "仅支持 .xlsx"},
        )
    if len(content) > settings.MAX_IMPORT_SIZE:
        fail(
            ErrorCode.PARAM_INVALID,
            f"文件不得超过 {settings.MAX_IMPORT_SIZE // (1024 * 1024)}MB",
            data={"field": "file", "reason": "文件过大"},
        )
    if not content.startswith(XLSX_MAGIC):
        fail(
            ErrorCode.PARAM_INVALID,
            "文件内容不是有效的 xlsx",
            data={"field": "file", "reason": "文件头校验失败"},
        )


def _cell_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def parse_workbook(content: bytes) -> tuple[list[dict], list[dict], int]:
    """返回 (有效行, 错误列表, 总行数)。"""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - 损坏文件
        fail(
            ErrorCode.PARAM_INVALID,
            f"无法解析 Excel：{exc}",
            data={"field": "file", "reason": "解析失败"},
        )
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        fail(ErrorCode.PARAM_INVALID, "Excel 内容为空", data={"field": "file", "reason": "无数据"})

    mapping: dict[int, str] = {}
    for idx, header in enumerate(header_row):
        key = _cell_text(header)
        if not key:
            continue
        if key in REQUIRED_HEADERS:
            mapping[idx] = REQUIRED_HEADERS[key]
        elif key.lower() in HEADER_ALIASES:
            mapping[idx] = HEADER_ALIASES[key.lower()]

    missing = [
        cn
        for cn, field in REQUIRED_HEADERS.items()
        if field in ("student_no", "name") and field not in mapping.values()
    ]
    if missing:
        fail(
            ErrorCode.PARAM_INVALID,
            f"缺少必要列：{'、'.join(missing)}",
            data={"field": "file", "reason": "缺少必要列"},
        )

    valid: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    total = 0

    for offset, raw in enumerate(rows, start=2):
        if raw is None or all(c is None or str(c).strip() == "" for c in raw):
            continue
        total += 1
        if total > settings.MAX_IMPORT_ROWS:
            fail(
                ErrorCode.PARAM_INVALID,
                f"单次导入不得超过 {settings.MAX_IMPORT_ROWS} 行",
                data={"field": "file", "reason": "行数超限"},
            )
        record = {field: None for field in REQUIRED_HEADERS.values()}
        for idx, field in mapping.items():
            if idx < len(raw):
                record[field] = _cell_text(raw[idx])

        reason = _validate_row(record, seen)
        if reason:
            errors.append({"row": offset, "student_no": record.get("student_no"), "reason": reason})
            continue
        seen.add(record["student_no"])
        valid.append(record)

    return valid, errors, total


def _validate_row(record: dict, seen: set[str]) -> str | None:
    student_no = record.get("student_no")
    if not student_no:
        return "学号不能为空"
    if not STUDENT_NO_RE.match(student_no):
        return "学号须为 6—20 位字母或数字"
    if student_no in seen:
        return "文件内学号重复"
    if not record.get("name"):
        return "姓名不能为空"
    if len(record["name"]) > 50:
        return "姓名过长"
    mobile = record.get("mobile")
    if mobile and not MOBILE_RE.match(mobile):
        return "手机号格式不正确"
    return None


def create_batch(
    db: Session,
    *,
    filename: str,
    valid: list[dict],
    errors: list[dict],
    total: int,
    operator_id: int,
    now: datetime | None = None,
) -> ImportBatch:
    """写入批次记录，并把待确认行放入缓存（30 分钟）。"""
    now = now or now_local()
    if errors and valid:
        status = ImportResult.PARTIAL
    elif errors and not valid:
        status = ImportResult.FAILED
    else:
        status = ImportResult.SUCCESS

    batch = ImportBatch(
        file_name=filename,
        operator_id=operator_id,
        total=total,
        success=len(valid),
        failed=len(errors),
        status=status,
        created_at=now,
    )
    db.add(batch)
    db.flush()

    # 失败明细写文件，地址存入 error_file_url（数据库文档 4.6）
    if errors:
        settings.ensure_dirs()
        path = settings.EXPORT_DIR / f"import_errors_{batch.id}.xlsx"
        exporter.create_error_list(path, errors)
        batch.error_file_url = f"/api/v1/admin/students/import/{batch.id}/errors"

    cache.set(
        _preview_key(batch.id),
        {"rows": valid, "errors": errors, "total": total},
        settings.IMPORT_BATCH_TTL,
    )
    return batch


def generate_initial_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(10))
        if any(c.isalpha() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


def confirm_batch(
    db: Session,
    batch: ImportBatch,
    *,
    duplicate_strategy: str,
    default_college: str | None = None,
    now: datetime | None = None,
) -> dict:
    """确认写入；duplicate_strategy=skip 跳过 / update 更新非空字段。"""
    now = now or now_local()
    preview = cache.get(_preview_key(batch.id))
    if not preview:
        fail(ErrorCode.ILLEGAL_STATE, "导入预览已过期（30 分钟），请重新上传")

    imported = updated = skipped = failed = 0
    password_list: list[dict] = []

    for record in preview["rows"]:
        student_no = record.get("student_no")
        existing = db.scalars(
            select(User).where(User.student_no == student_no, User.deleted_at.is_(None))
        ).first()
        try:
            if existing is not None:
                if duplicate_strategy == "skip":
                    skipped += 1
                    continue
                for field in ("name", "college", "major", "class_name", "grade", "mobile"):
                    value = record.get(field)
                    if value:
                        setattr(existing, field, value)
                if default_college and not record.get("college"):
                    existing.college = default_college
                existing.import_batch_id = batch.id
                db.add(existing)
                updated += 1
                continue

            password = generate_initial_password()
            user = User(
                student_no=student_no,
                name=record.get("name") or "",
                college=record.get("college") or default_college or "",
                major=record.get("major"),
                class_name=record.get("class_name"),
                grade=int(record["grade"]) if record.get("grade") and str(record["grade"]).isdigit() else None,
                mobile=record.get("mobile"),
                password_hash=hash_password(password),
                status=AccountStatus.ACTIVE,
                is_first_login=True,
                import_batch_id=batch.id,
                login_fail_count=0,
            )
            db.add(user)
            password_list.append({"student_no": student_no, "name": user.name, "password": password})
            imported += 1
        except Exception as exc:  # pragma: no cover - 单行失败不影响整体
            failed += 1
            preview["errors"].append({"row": None, "student_no": student_no, "reason": str(exc)})

    batch.success = batch.success + imported + updated
    batch.failed = batch.failed + failed
    db.add(batch)
    db.flush()

    password_file = None
    if password_list:
        settings.ensure_dirs()
        path = settings.EXPORT_DIR / f"passwords_{batch.id}.xlsx"
        exporter.create_password_list(path, password_list)
        password_file = f"/api/v1/admin/students/import/{batch.id}/passwords"

    cache.delete(_preview_key(batch.id))

    return {
        "batch_id": batch.id,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "password_list_available": bool(password_file),
        "password_count": len(password_list),
        "password_file": password_file,
    }


def password_file_path(batch_id: int) -> Path:
    return settings.EXPORT_DIR / f"passwords_{batch_id}.xlsx"


def error_file_path(batch_id: int) -> Path:
    return settings.EXPORT_DIR / f"import_errors_{batch_id}.xlsx"
