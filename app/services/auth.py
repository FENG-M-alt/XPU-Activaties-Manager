"""认证与会话（接口文档 2.2 / 第 3 章；数据库文档 4.1 / 4.2）。

登录失败锁定改为使用 users / admins 表的 login_fail_count 与 locked_until
（数据库文档 1.2 差异说明 2），不再单独落登录尝试表；
策略从 settings 表的 student_login_lock / admin_login_lock 读取，格式 "次数:分钟"。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import cache
from app.core.enums import AccountStatus, AccountType, AdminRole, DataScope
from app.core.errors import ErrorCode, fail
from app.core.security import (
    create_access_token,
    generate_captcha_code,
    hash_password,
    hash_token,
    password_needs_rehash,
    random_token,
    render_captcha_svg,
    svg_to_data_url,
    verify_password,
)
from app.core.timeutil import now_local
from app.models.admin import Admin
from app.models.setting import Setting
from app.models.supplement import AuthSession
from app.models.user import User


# ---------------------------------------------------------------- 会话
def _revoke_expired(db: Session, account_type: str, account_id: int) -> None:
    db.execute(
        update(AuthSession)
        .where(
            AuthSession.account_type == account_type,
            AuthSession.account_id == account_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at <= now_local(),
        )
        .values(revoked_at=now_local())
    )


def enforce_session_limit(db: Session, account_type: str, account_id: int) -> None:
    """同一账号最多 3 个有效会话，超出后最早会话失效（接口文档 2.2）。"""
    _revoke_expired(db, account_type, account_id)
    sessions = db.scalars(
        select(AuthSession)
        .where(
            AuthSession.account_type == account_type,
            AuthSession.account_id == account_id,
            AuthSession.revoked_at.is_(None),
        )
        .order_by(AuthSession.issued_at.asc())
    ).all()
    overflow = len(sessions) - settings.MAX_SESSIONS_PER_ACCOUNT + 1
    if overflow > 0:
        for old in sessions[:overflow]:
            old.revoked_at = now_local()


def create_session(
    db: Session,
    *,
    account_type: str,
    account_id: int,
    request: Request | None = None,
    role: str | None = None,
    now: datetime | None = None,
) -> tuple[str, int, str]:
    """创建会话，返回 (access_token, expires_in, refresh_token)。"""
    now = now or now_local()
    enforce_session_limit(db, account_type, account_id)

    session_id = random_token(32)
    refresh_token = random_token(64)
    session = AuthSession(
        session_id=session_id,
        account_type=account_type,
        account_id=account_id,
        refresh_hash=hash_token(refresh_token),
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.REFRESH_TOKEN_TTL),
        ip=_ip(request),
        user_agent=_ua(request),
        last_used_at=now,
    )
    db.add(session)

    access_token, expires_in = create_access_token(
        subject=account_id,
        account_type=account_type,
        session_id=session_id,
        role=role,
    )
    return access_token, expires_in, refresh_token


def set_refresh_cookie(response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_TTL,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path=settings.COOKIE_PATH,
        domain=settings.COOKIE_DOMAIN,
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path=settings.COOKIE_PATH,
        domain=settings.COOKIE_DOMAIN,
    )


def revoke_session_by_id(db: Session, session_id: str) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.session_id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now_local())
    )


def revoke_session_by_refresh(db: Session, refresh_token: str) -> bool:
    token_hash = hash_token(refresh_token)
    session = db.scalars(
        select(AuthSession).where(
            AuthSession.refresh_hash == token_hash, AuthSession.revoked_at.is_(None)
        )
    ).first()
    if not session:
        return False
    session.revoked_at = now_local()
    return True


def revoke_other_sessions(
    db: Session, account_type: str, account_id: int, keep_session_id: str | None
) -> None:
    stmt = update(AuthSession).where(
        AuthSession.account_type == account_type,
        AuthSession.account_id == account_id,
        AuthSession.revoked_at.is_(None),
    )
    if keep_session_id:
        stmt = stmt.where(AuthSession.session_id != keep_session_id)
    db.execute(stmt.values(revoked_at=now_local()))


def get_session(db: Session, session_id: str) -> AuthSession | None:
    return db.scalars(select(AuthSession).where(AuthSession.session_id == session_id)).first()


def refresh_access_token(
    db: Session,
    refresh_token: str | None,
    *,
    now: datetime | None = None,
) -> tuple[str, int, AuthSession]:
    """凭 Cookie 中的 refresh token 换取新的 access token（接口文档 3.2）。"""
    now = now or now_local()
    if not refresh_token:
        fail(ErrorCode.UNAUTHORIZED, "缺少刷新令牌，请重新登录")

    token_hash = hash_token(refresh_token)
    session = db.scalars(select(AuthSession).where(AuthSession.refresh_hash == token_hash)).first()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        fail(ErrorCode.UNAUTHORIZED, "登录已失效，请重新登录")

    account, role = _load_account(db, session.account_type, session.account_id)
    if account is None:
        fail(ErrorCode.UNAUTHORIZED)
    if account.status != AccountStatus.ACTIVE:
        fail(ErrorCode.ACCOUNT_DISABLED)

    session.last_used_at = now
    access_token, expires_in = create_access_token(
        subject=session.account_id,
        account_type=session.account_type,
        session_id=session.session_id,
        role=role,
    )
    return access_token, expires_in, session


def _load_account(db: Session, account_type: str, account_id: int):
    if account_type == AccountType.USER:
        return db.get(User, account_id), None
    admin = db.get(Admin, account_id)
    return admin, (admin.role if admin else None)


# ---------------------------------------------------------------- 锁定策略
def _lock_policy(db: Session, account_type: str) -> tuple[int, int]:
    """返回 (最大失败次数, 锁定分钟数)；优先取 settings 表配置。"""
    key = "student_login_lock" if account_type == AccountType.USER else "admin_login_lock"
    fallback_max = settings.LOGIN_MAX_FAILURES
    fallback_minutes = (
        settings.STUDENT_LOCK_MINUTES
        if account_type == AccountType.USER
        else settings.ADMIN_LOCK_MINUTES
    )
    row = db.get(Setting, key)
    if row and row.value and ":" in row.value:
        left, _, right = row.value.partition(":")
        try:
            return max(int(left), 1), max(int(right), 1)
        except ValueError:
            pass
    return fallback_max, fallback_minutes


def _ensure_not_locked(account) -> None:
    now = now_local()
    if account.locked_until and account.locked_until > now:
        remaining = max(int((account.locked_until - now).total_seconds() // 60), 1)
        fail(ErrorCode.LOGIN_LOCKED, f"失败次数过多，请 {remaining} 分钟后重试")


def _on_login_failure(db: Session, account, account_type: str) -> None:
    """累加失败次数，达到阈值则写入锁定截止时间（users/admins 表的字段）。"""
    max_failures, lock_minutes = _lock_policy(db, account_type)
    account.login_fail_count = int(account.login_fail_count or 0) + 1
    if account.login_fail_count >= max_failures:
        account.locked_until = now_local() + timedelta(minutes=lock_minutes)
        account.login_fail_count = 0
    db.add(account)
    db.commit()
    if account.locked_until and account.locked_until > now_local():
        fail(ErrorCode.LOGIN_LOCKED, f"失败次数过多，请 {lock_minutes} 分钟后重试")


def _on_login_success(db: Session, account, request: Request | None) -> None:
    account.login_fail_count = 0
    account.locked_until = None
    account.last_login_at = now_local()
    account.last_login_ip = _ip(request)
    db.add(account)


# ---------------------------------------------------------------- 学生登录
def login_student(db: Session, student_no: str, password: str, request: Request | None = None) -> dict:
    user = db.scalars(
        select(User).where(User.student_no == student_no, User.deleted_at.is_(None))
    ).first()

    if user is None:
        fail(ErrorCode.BAD_CREDENTIALS, "学号或密码错误")

    _ensure_not_locked(user)

    if not verify_password(password, user.password_hash):
        _on_login_failure(db, user, AccountType.USER)
        fail(ErrorCode.BAD_CREDENTIALS, "学号或密码错误")

    if user.status != AccountStatus.ACTIVE:
        _on_login_failure(db, user, AccountType.USER)
        fail(ErrorCode.ACCOUNT_DISABLED)

    # 历史 PBKDF2 哈希在登录成功时平滑升级为 bcrypt
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    _on_login_success(db, user, request)
    access_token, expires_in, refresh_token = create_session(
        db, account_type=AccountType.USER, account_id=user.id, request=request
    )
    db.commit()
    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "is_first_login": user.is_first_login,
        "user": user,
    }


# ---------------------------------------------------------------- 验证码
def issue_captcha() -> dict:
    code = generate_captcha_code()
    captcha_id = random_token(32)
    cache.set(f"captcha:{captcha_id}", code.upper(), settings.CAPTCHA_TTL)
    return {
        "captcha_id": captcha_id,
        "image": svg_to_data_url(render_captcha_svg(code)),
        "expires_in": settings.CAPTCHA_TTL,
    }


def verify_captcha(captcha_id: str, captcha_code: str) -> bool:
    """5 分钟有效、一次使用失效（接口文档 3.6）。"""
    if not captcha_id or not captcha_code:
        return False
    expected = cache.pop(f"captcha:{captcha_id}")
    if not expected:
        return False
    from app.core.security import constant_time_equals

    return constant_time_equals(captcha_code.strip().upper(), expected)


# ---------------------------------------------------------------- 管理员登录
def login_admin(
    db: Session,
    username: str,
    password: str,
    captcha_id: str,
    captcha_code: str,
    request: Request | None = None,
) -> dict:
    if not verify_captcha(captcha_id, captcha_code):
        fail(
            ErrorCode.PARAM_INVALID,
            "验证码错误或已失效",
            data={"field": "captcha_code", "reason": "验证码错误或已失效"},
        )

    admin = db.scalars(
        select(Admin).where(Admin.username == username, Admin.deleted_at.is_(None))
    ).first()
    if admin is None:
        fail(ErrorCode.BAD_CREDENTIALS, "账号或密码错误")

    _ensure_not_locked(admin)

    if not verify_password(password, admin.password_hash):
        _on_login_failure(db, admin, AccountType.ADMIN)
        fail(ErrorCode.BAD_CREDENTIALS, "账号或密码错误")

    if admin.status != AccountStatus.ACTIVE:
        _on_login_failure(db, admin, AccountType.ADMIN)
        fail(ErrorCode.ACCOUNT_DISABLED)

    if password_needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)

    _on_login_success(db, admin, request)
    access_token, expires_in, refresh_token = create_session(
        db,
        account_type=AccountType.ADMIN,
        account_id=admin.id,
        request=request,
        role=admin.role,
    )
    db.commit()
    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "is_first_login": admin.is_first_login,
        "user": admin,
    }


# ---------------------------------------------------------------- 修改密码
def change_password(
    db: Session,
    account,
    *,
    account_type: str,
    old_password: str,
    new_password: str,
    keep_session_id: str | None,
) -> None:
    from app.core.security import password_strength_error

    if not verify_password(old_password, account.password_hash):
        fail(
            ErrorCode.PARAM_INVALID,
            "原密码不正确",
            data={"field": "old_password", "reason": "原密码不正确"},
        )
    reference = getattr(account, "student_no", None) or getattr(account, "username", None)
    reason = password_strength_error(new_password, student_no=reference, old_password=old_password)
    if reason:
        fail(ErrorCode.PARAM_INVALID, reason, data={"field": "new_password", "reason": reason})

    account.password_hash = hash_password(new_password)
    account.is_first_login = False
    db.add(account)
    # 修改密码后其他会话立即失效（接口文档 2.2）
    revoke_other_sessions(db, account_type, account.id, keep_session_id)
    db.commit()


# ---------------------------------------------------------------- 辅助
def _ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _ua(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:255] if ua else None


# 管理员权限点（接口文档 A-02）
PERMISSIONS: dict[str, list[str]] = {
    AdminRole.SUPER_ADMIN: [
        "dashboard:view",
        "activity:view",
        "activity:create",
        "activity:edit",
        "activity:publish",
        "activity:cancel",
        "activity:finish",
        "activity:delete",
        "activity:reset_code",
        "registration:view",
        "registration:audit",
        "registration:checkin",
        "registration:undo_checkin",
        "export:download",
        "student:view",
        "student:import",
        "student:manage",
        "stats:view",
        "dict:view",
        "dict:manage",
        "setting:view",
        "setting:manage",
        "log:view",
        "log:export",
        "admin:manage",
    ],
    AdminRole.ADMIN: [
        "dashboard:view",
        "activity:view",
        "activity:create",
        "activity:edit",
        "activity:publish",
        "activity:cancel",
        "activity:finish",
        "activity:delete",
        "activity:reset_code",
        "registration:view",
        "registration:audit",
        "registration:checkin",
        "export:download",
        "student:view",
        "student:import",
        "stats:view",
        "dict:view",
        "setting:view",
        "log:view",
    ],
}


def permissions_for(admin: Admin) -> list[str]:
    return PERMISSIONS.get(admin.role, PERMISSIONS[AdminRole.ADMIN])


def scope_label(admin: Admin) -> str:
    return {
        DataScope.ALL: "全校",
        DataScope.COLLEGE: "指定学院",
        DataScope.SELF: "本人创建",
    }.get(admin.data_scope, admin.data_scope)
