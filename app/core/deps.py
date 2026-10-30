"""FastAPI 依赖：数据库、当前用户、权限与数据范围（文档 2.7）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.enums import AccountStatus, AdminRole, DataScope
from app.core.errors import ErrorCode, fail
from app.core.security import decode_token
from app.database import get_db
from app.models.admin import Admin
from app.models.student import Student
from app.services.auth import get_session

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(slots=True)
class Principal:
    """已认证主体。"""

    account_type: str
    id: int
    session_id: str
    student: Student | None = None
    admin: Admin | None = None


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _resolve_principal(
    db: Session,
    authorization: str | None,
    *,
    required_type: str | None = None,
    optional: bool = False,
) -> Principal | None:
    token = _bearer_token(authorization)
    if not token:
        if optional:
            return None
        fail(ErrorCode.UNAUTHORIZED)

    payload = decode_token(token)
    if not payload or payload.get("scope") != "access":
        if optional:
            return None
        fail(ErrorCode.UNAUTHORIZED)

    account_type = payload.get("typ")
    if required_type and account_type != required_type:
        # 学生令牌访问管理端接口一律 403（文档 2.2）
        fail(ErrorCode.FORBIDDEN)

    session_id = payload.get("sid")
    subject = payload.get("sub")
    if not session_id or not subject:
        if optional:
            return None
        fail(ErrorCode.UNAUTHORIZED)

    session = get_session(db, session_id)
    if session is None or not session.is_valid:
        if optional:
            return None
        fail(ErrorCode.UNAUTHORIZED, "登录已失效，请重新登录")

    try:
        account_id = int(subject)
    except (TypeError, ValueError):
        fail(ErrorCode.UNAUTHORIZED)

    if account_type == "student":
        student = db.get(Student, account_id)
        if student is None:
            fail(ErrorCode.UNAUTHORIZED)
        if student.status != AccountStatus.ACTIVE:
            fail(ErrorCode.ACCOUNT_DISABLED)
        if session.account_id != student.id:
            fail(ErrorCode.UNAUTHORIZED)
        return Principal(account_type="student", id=student.id, session_id=session_id, student=student)

    if account_type == "admin":
        admin = db.get(Admin, account_id)
        if admin is None:
            fail(ErrorCode.UNAUTHORIZED)
        if admin.status != AccountStatus.ACTIVE:
            fail(ErrorCode.ACCOUNT_DISABLED)
        if session.account_id != admin.id:
            fail(ErrorCode.UNAUTHORIZED)
        return Principal(account_type="admin", id=admin.id, session_id=session_id, admin=admin)

    fail(ErrorCode.UNAUTHORIZED)


def get_current_student(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Student:
    principal = _resolve_principal(db, authorization, required_type="student")
    assert principal and principal.student is not None
    return principal.student


def get_optional_student(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Student | None:
    principal = _resolve_principal(db, authorization, required_type="student", optional=True)
    return principal.student if principal else None


def get_current_admin(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Admin:
    principal = _resolve_principal(db, authorization, required_type="admin")
    assert principal and principal.admin is not None
    return principal.admin


def get_current_principal(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal:
    principal = _resolve_principal(db, authorization)
    assert principal is not None
    return principal


def get_optional_principal(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal | None:
    """可选认证：无令牌或令牌失效时返回 None，不抛异常（用于退出登录等）。"""
    return _resolve_principal(db, authorization, optional=True)


def require_super_admin(admin: Annotated[Admin, Depends(get_current_admin)]) -> Admin:
    if admin.role != AdminRole.SUPER_ADMIN:
        fail(ErrorCode.FORBIDDEN, "该操作仅超级管理员可执行")
    return admin


CurrentStudent = Annotated[Student, Depends(get_current_student)]
OptionalStudent = Annotated[Student | None, Depends(get_optional_student)]
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]
SuperAdmin = Annotated[Admin, Depends(require_super_admin)]
PrincipalDep = Annotated[Principal, Depends(get_current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]


# ---------------------------------------------------------------- 数据范围
def activity_scope_conditions(admin: Admin) -> list:
    """管理端查询按 data_scope 过滤（文档 2.7）。"""
    from app.models.activity import Activity

    if admin.role == AdminRole.SUPER_ADMIN or admin.data_scope == DataScope.ALL:
        return []
    if admin.data_scope == DataScope.COLLEGE:
        conditions = [Activity.created_by == admin.id]
        if admin.college:
            conditions.append(Activity.college == admin.college)
        return [or_(*conditions)]
    return [Activity.created_by == admin.id]


def can_access_activity(admin: Admin, activity) -> bool:
    if admin.role == AdminRole.SUPER_ADMIN or admin.data_scope == DataScope.ALL:
        return True
    if admin.data_scope == DataScope.COLLEGE:
        return activity.created_by == admin.id or (admin.college is not None and activity.college == admin.college)
    return activity.created_by == admin.id


def ensure_activity_access(admin: Admin, activity) -> None:
    if not can_access_activity(admin, activity):
        fail(ErrorCode.FORBIDDEN)


def ensure_student_scope(admin: Admin, student: Student) -> None:
    """管理员访问学生数据的范围校验。"""
    if admin.role == AdminRole.SUPER_ADMIN or admin.data_scope == DataScope.ALL:
        return
    if admin.data_scope == DataScope.COLLEGE:
        if admin.college and student.college == admin.college:
            return
    fail(ErrorCode.FORBIDDEN)
