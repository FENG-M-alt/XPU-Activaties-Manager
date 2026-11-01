"""FastAPI 依赖：数据库、当前用户、权限与数据范围（文档 2.7）。

账号类型统一使用 ``user`` / ``admin``（对应 users / admins 两张表）。
数据范围（data_scope）：
- ALL     全校数据；
- COLLEGE 本学院数据 —— activities 表无学院字段，按"创建人属于本学院"过滤，
          报名/学生数据按快照字段或 users.college 过滤；
- SELF    仅本人创建。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.enums import AccountStatus, AccountType, AdminRole, DataScope
from app.core.errors import ErrorCode, fail
from app.core.security import decode_token
from app.database import get_db
from app.models.admin import Admin
from app.models.user import User
from app.services.auth import get_session

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(slots=True)
class Principal:
    """已认证主体。"""

    account_type: str
    id: int
    session_id: str
    user: User | None = None
    admin: Admin | None = None

    @property
    def student(self) -> User | None:
        """兼容旧调用：学生主体即 user。"""
        return self.user


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

    if account_type == AccountType.USER:
        user = db.get(User, account_id)
        if user is None or user.deleted_at is not None:
            fail(ErrorCode.UNAUTHORIZED)
        if user.status != AccountStatus.ACTIVE:
            fail(ErrorCode.ACCOUNT_DISABLED)
        if session.account_id != user.id:
            fail(ErrorCode.UNAUTHORIZED)
        return Principal(account_type=AccountType.USER, id=user.id, session_id=session_id, user=user)

    if account_type == AccountType.ADMIN:
        admin = db.get(Admin, account_id)
        if admin is None or admin.deleted_at is not None:
            fail(ErrorCode.UNAUTHORIZED)
        if admin.status != AccountStatus.ACTIVE:
            fail(ErrorCode.ACCOUNT_DISABLED)
        if session.account_id != admin.id:
            fail(ErrorCode.UNAUTHORIZED)
        return Principal(account_type=AccountType.ADMIN, id=admin.id, session_id=session_id, admin=admin)

    fail(ErrorCode.UNAUTHORIZED)


def get_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> User:
    principal = _resolve_principal(db, authorization, required_type=AccountType.USER)
    assert principal and principal.user is not None
    return principal.user


def get_optional_user(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> User | None:
    principal = _resolve_principal(db, authorization, required_type=AccountType.USER, optional=True)
    return principal.user if principal else None


def get_current_admin(
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Admin:
    principal = _resolve_principal(db, authorization, required_type=AccountType.ADMIN)
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


# 学生端主体（保留 CurrentStudent 命名，语义即 users 表记录）
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
CurrentStudent = CurrentUser
OptionalStudent = OptionalUser
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]
SuperAdmin = Annotated[Admin, Depends(require_super_admin)]
PrincipalDep = Annotated[Principal, Depends(get_current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]


# ---------------------------------------------------------------- 数据范围
def _college_admin_ids(db: Session, college: str) -> list[int]:
    """本学院管理员的 id 列表（activities 无学院字段，用创建人归属推断）。"""
    rows = db.scalars(
        select(Admin.id).where(Admin.college == college, Admin.deleted_at.is_(None))
    ).all()
    return list(rows)


def activity_scope_conditions(admin: Admin, db: Session | None = None) -> list:
    """管理端活动查询按 data_scope 过滤（文档 2.7）。"""
    from app.models.activity import Activity

    if admin.role == AdminRole.SUPER_ADMIN or admin.data_scope == DataScope.ALL:
        return []
    if admin.data_scope == DataScope.COLLEGE:
        conditions = [Activity.created_by == admin.id]
        if admin.college and db is not None:
            ids = _college_admin_ids(db, admin.college)
            if ids:
                conditions.append(Activity.created_by.in_(ids))
        return [or_(*conditions)]
    return [Activity.created_by == admin.id]


def can_access_activity(admin: Admin, activity, db: Session | None = None) -> bool:
    if admin.role == AdminRole.SUPER_ADMIN or admin.data_scope == DataScope.ALL:
        return True
    if activity.created_by == admin.id:
        return True
    if admin.data_scope == DataScope.COLLEGE and admin.college and db is not None:
        creator = db.get(Admin, activity.created_by) if activity.created_by else None
        return bool(creator and creator.college == admin.college)
    return False


def ensure_activity_access(admin: Admin, activity, db: Session | None = None) -> None:
    if not can_access_activity(admin, activity, db):
        fail(ErrorCode.FORBIDDEN)


def ensure_user_scope(admin: Admin, user: User) -> None:
    """管理员访问学生数据的范围校验（按 users.college）。"""
    if admin.role == AdminRole.SUPER_ADMIN or admin.data_scope == DataScope.ALL:
        return
    if admin.data_scope == DataScope.COLLEGE and admin.college and user.college == admin.college:
        return
    fail(ErrorCode.FORBIDDEN)


# 兼容旧命名
ensure_student_scope = ensure_user_scope
