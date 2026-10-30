"""学生 Excel 导入：解析预览 + 确认写入（文档 A-23 / A-24 / 2.8）。"""
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
from app.core.enums import AccountStatus, ImportResult
from app.core.errors import ErrorCode, fail
from app.core.security import hash_password
from app.core.timeutil import now_local
from app.models.student import Student
from app.models.system import ImportBatch

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


class ImportValidationError(Exception):
    pass


def validate_upload(filename: str | None, content: bytes) -> None:
    if not filename or not filename.lower().endswith(".xlsx"):
        fail(ErrorCode.PARAM_INVALID, "仅支持 .xlsx 文件", data={"field": "file", "reason": "仅支持 .xlsx"})
    if len(content) > settings.MAX_IMPORT_SIZE:
        fail(ErrorCode.PARAM_INVALID, f"文件不得超过 {settings.MAX_IMPORT_SIZE // (1024 * 1024)}MB",
             data={"field": "file", "reason": "文件过大"})
    if not content.startswith(XLSX_MAGIC):
        fail(ErrorCode.PARAM_INVALID, "文件内容不是有效的 xlsx",
             data={"field": "file", "reason": "文件头校验失败"})


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
        fail(ErrorCode.PARAM_INVALID, f"无法解析 Excel：{exc}", data={"field": "file", "reason": "解析失败"})
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

    missing = [cn for cn, field in REQUIRED_HEADERS.items() if field not in mapping.values() and field in ("student_no", "name")]
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
            fail(ErrorCode.PARAM_INVALID, f"单次导入不得超过 {settings.MAX_IMPORT_ROWS} 行",
                 data={"field": "file", "reason": "行数超限"})
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
    admin_id: int,
    now: datetime | None = None,
) -> ImportBatch:
    now = now or now_local()
    batch_no = secrets.token_hex(10)
    if errors and valid:
        status = ImportResult.PARTIAL
    elif errors and not valid:
        status = ImportResult.FAILED
    else:
        status = ImportResult.SUCCESS

    batch = ImportBatch(
        batch_id=batch_no,
        filename=filename,
        total=total,
        success=len(valid),
        failed=len(errors),
        status=status,
        rows=valid,
        errors=errors,
        confirmed=False,
        created_by=admin_id,
        expires_at=now + timedelta(seconds=settings.IMPORT_BATCH_TTL),
    )
    db.add(batch)
    db.flush()
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
    if batch.confirmed:
        fail(ErrorCode.ILLEGAL_STATE, "该批次已确认写入")
    if batch.expires_at <= now:
        fail(ErrorCode.ILLEGAL_STATE, "导入批次已过期，请重新上传")

    imported = updated = skipped = failed = 0
    password_list: list[dict] = []
    failed_rows: list[dict] = list(batch.errors or [])

    for record in batch.rows or []:
        student_no = record.get("student_no")
        existing = db.scalars(select(Student).where(Student.student_no == student_no)).first()
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
                db.add(existing)
                updated += 1
                continue

            password = generate_initial_password()
            student = Student(
                student_no=student_no,
                name=record.get("name") or "",
                college=record.get("college") or default_college,
                major=record.get("major"),
                class_name=record.get("class_name"),
                grade=record.get("grade"),
                mobile=record.get("mobile"),
                password_hash=hash_password(password),
                status=AccountStatus.ACTIVE,
                is_first_login=True,
            )
            db.add(student)
            password_list.append({"student_no": student_no, "name": student.name, "password": password})
            imported += 1
        except Exception as exc:  # pragma: no cover - 单行失败不影响整体
            failed += 1
            failed_rows.append({"row": None, "student_no": student_no, "reason": str(exc)})

    batch.confirmed = True
    batch.duplicate_strategy = duplicate_strategy
    batch.password_list = password_list
    batch.errors = failed_rows
    batch.success = imported + updated
    batch.failed = failed + skipped
    db.add(batch)
    db.flush()

    return {
        "batch_id": batch.batch_id,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "password_list_available": bool(password_list),
        "password_count": len(password_list),
    }
