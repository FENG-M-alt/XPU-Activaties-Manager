"""Excel 导出（接口文档 A-21 / A-32）。

学时来自 activities.hours（报名表不存学时），导出任务记录落在 export_tasks。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import ExportTaskStatus, RegistrationStatus
from app.core.timeutil import now_local, to_iso
from app.models.activity import Activity
from app.models.registration import Registration
from app.models.supplement import ExportTask

HEADER_FILL = PatternFill("solid", fgColor="E8EEF9")
HEADER_FONT = Font(bold=True)

REGISTRATION_HEADERS = [
    "学号",
    "姓名",
    "学院",
    "班级",
    "报名时间",
    "签到时间",
    "签到方式",
    "签到状态",
    "学时",
]
STUDENT_HEADERS = [
    "学号",
    "姓名",
    "学院",
    "专业",
    "班级",
    "年级",
    "手机号",
    "状态",
    "累计参与次数",
    "累计学时",
]

STATUS_LABELS = {
    RegistrationStatus.PENDING: "待审核",
    RegistrationStatus.APPROVED: "已通过",
    RegistrationStatus.REJECTED: "已拒绝",
    RegistrationStatus.CANCELLED: "已取消",
    RegistrationStatus.CHECKED_IN: "已签到",
    RegistrationStatus.COMPLETED: "已完成",
    RegistrationStatus.ABSENT: "缺席",
}
METHOD_LABELS = {"CODE": "签到码", "MANUAL": "管理员补签"}
COUNTED_STATUSES = (RegistrationStatus.CHECKED_IN, RegistrationStatus.COMPLETED)


def _style_sheet(ws, headers: list[str]) -> None:
    ws.append(headers)
    for idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = max(
            12, len(str(headers[idx - 1])) * 2 + 4
        )
    ws.freeze_panes = "A2"


def _write_workbook(rows: list[list], headers: list[str], sheet_title: str = "Sheet1") -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    _style_sheet(ws, headers)
    for row in rows:
        ws.append(row)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="center")
    return wb


def _fmt(dt: datetime | None) -> str | None:
    return to_iso(dt) if dt else None


def _hours_of(registration: Registration) -> float:
    activity = registration.activity
    if activity is None or registration.status not in COUNTED_STATUSES:
        return 0.0
    return float(activity.hours or 0)


def registration_rows(registrations: list[Registration]) -> list[list]:
    rows = []
    for r in registrations:
        user = r.user
        rows.append(
            [
                user.student_no if user else "",
                user.name if user else "",
                # 用报名时的学院快照，避免学生转专业后导出漂移
                r.college_snapshot or (user.college if user else ""),
                r.class_snapshot or (user.class_name if user else ""),
                _fmt(r.signup_at),
                _fmt(r.checkin_at) or "",
                METHOD_LABELS.get(r.checkin_method or "", "") if r.checkin_method else "",
                STATUS_LABELS.get(r.status, r.status),
                _hours_of(r),
            ]
        )
    return rows


def export_filename(activity: Activity, now: datetime | None = None) -> str:
    now = now or now_local()
    return f"{activity.title}_签到表_{now.strftime('%Y%m%d')}.xlsx"


def create_registration_export(
    db: Session,
    activity: Activity,
    registrations: list[Registration],
    *,
    task_id: str,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> ExportTask:
    """生成导出文件并登记任务；文件保留 EXPORT_FILE_TTL_DAYS 天（接口文档 A-32）。"""
    now = now or now_local()
    settings.ensure_dirs()
    filename = export_filename(activity, now)
    path = settings.EXPORT_DIR / f"{task_id}.xlsx"

    wb = _write_workbook(registration_rows(registrations), REGISTRATION_HEADERS, "报名与签到名单")
    wb.save(path)

    return ExportTask(
        task_id=task_id,
        activity_id=activity.id,
        export_type="registrations",
        file_name=filename,
        file_path=str(path),
        row_count=len(registrations),
        status=ExportTaskStatus.DONE,
        created_by=operator_id,
        created_at=now,
        expires_at=now + timedelta(days=settings.EXPORT_FILE_TTL_DAYS),
    )


def create_students_export(path: Path, students: list[dict]) -> Path:
    headers = STUDENT_HEADERS
    rows = [
        [
            s.get("student_no"),
            s.get("name"),
            s.get("college"),
            s.get("major"),
            s.get("class_name"),
            s.get("grade"),
            s.get("mobile"),
            "正常" if s.get("status") == "ACTIVE" else "停用",
            s.get("activity_count", 0),
            s.get("hours_total", 0),
        ]
        for s in students
    ]
    wb = _write_workbook(rows, headers, "学生名单")
    wb.save(path)
    return path


def create_password_list(path: Path, entries: list[dict]) -> Path:
    headers = ["学号", "姓名", "初始密码"]
    rows = [[e.get("student_no"), e.get("name"), e.get("password")] for e in entries]
    wb = _write_workbook(rows, headers, "初始密码清单")
    wb.save(path)
    return path


def create_error_list(path: Path, errors: list[dict]) -> Path:
    """导入失败明细文件（写入 import_batches.error_file_url 指向的文件）。"""
    headers = ["行号", "学号", "失败原因"]
    rows = [[e.get("row"), e.get("student_no"), e.get("reason")] for e in errors]
    wb = _write_workbook(rows, headers, "失败明细")
    wb.save(path)
    return path


def create_generic_export(
    path: Path, headers: list[str], rows: list[list], title: str = "Sheet1"
) -> Path:
    """通用导出（操作日志等）。"""
    wb = _write_workbook(rows, headers, title)
    wb.save(path)
    return path
