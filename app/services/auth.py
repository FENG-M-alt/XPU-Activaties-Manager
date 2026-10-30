"""认证与会话（文档 2.2 / 第 3 章）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.cache import cache
from app.core.enums import AccountStatus, AdminRole, DataScope
from app.core.errors import ErrorCode, fail
from app.core.security import (
    create_access_token,
    generate_captcha_code,
    hash_password,
    hash_token,
    random_token,
    render_captcha_svg,
    svg_to_data_url,
    verify_password,
)
from app.core.timeutil import now_local
from app.models.admin import Admin
from app.models.audit import AuthSession, LoginAttempt
from app.models.student import Student


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
    """同一账号最多 3 个有效会话，超出后最早会话失效（文档 2.2）。"""
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
        select(AuthSession).where(AuthSession.refresh_hash == token_hash, AuthSession.revoked_at.is_(None))
    ).first()
    if not session:
        return False
    session.revoked_at = now_local()
    return True


def revoke_other_sessions(db: Session, account_type: str, account_id: int, keep_session_id: str | None) -> None:
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
    """凭 Cookie 中的 refresh token 换取新的 access token（文档 3.2）。"""
    now = now or now_local()
    if not refresh_token:
        fail(ErrorCode.UNAUTHORIZED, "缺少刷新令牌，请重新登录")

    token_hash = hash_token(refresh_token)
    session = db.scalars(
        select(AuthSession).where(AuthSession.refresh_hash == token_hash)
    ).first()
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
    if account_type == "student":
        student = db.get(Student, account_id)
        return student, None
    admin = db.get(Admin, account_id)
    return admin, (admin.role if admin else None)


# ---------------------------------------------------------------- 失败锁定
def _since(minutes: int, now: datetime) -> datetime:
    return now - timedelta(minutes=minutes)


def failure_count(db: Session, account_type: str, account: str, minutes: int, now: datetime) -> int:
    from sqlalchemy import func

    return int(
        db.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.account_type == account_type,
                LoginAttempt.account == account,
                LoginAttempt.success.is_(False),
                LoginAttempt.created_at >= _since(minutes, now),
            )
        )
        or 0
    )


def last_failure_at(db: Session, account_type: str, account: str) -> datetime | None:
    from sqlalchemy import func

    return db.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.account_type == account_type,
            LoginAttempt.account == account,
            LoginAttempt.success.is_(False),
        )
    )


def check_locked(db: Session, account_type: str, account: str, lock_minutes: int, now: datetime) -> None:
    failures = failure_count(db, account_type, account, lock_minutes, now)
    if failures >= settings.LOGIN_MAX_FAILURES:
        last = last_failure_at(db, account_type, account)
        remaining = lock_minutes
        if last:
            remaining = max(int((last + timedelta(minutes=lock_minutes) - now).total_seconds() // 60), 1)
        fail(ErrorCode.LOGIN_LOCKED, f"失败次数过多，请 {remaining} 分钟后重试")


def record_attempt(
    db: Session,
    *,
    account_type: str,
    account: str,
    success: bool,
    request: Request | None,
    now: datetime,
) -> int:
    db.add(
        LoginAttempt(
            account_type=account_type,
            account=account,
            ip=_ip(request),
            success=success,
            user_agent=_ua(request),
            created_at=now,
        )
    )
    db.commit()
    if success:
        return 0
    return failure_count(db, account_type, account, settings.STUDENT_LOCK_MINUTES, now)


# ---------------------------------------------------------------- 学生登录
def login_student(db: Session, student_no: str, password: str, request: Request | None = None) -> dict:
    now = now_local()
    check_locked(db, "student", student_no, settings.STUDENT_LOCK_MINUTES, now)

    student = db.scalars(select(Student).where(Student.student_no == student_no)).first()
    if student is None or not verify_password(password, student.password_hash):
        count = record_attempt(db, account_type="student", account=student_no, success=False, request=request, now=now)
        if count >= settings.LOGIN_MAX_FAILURES:
            fail(ErrorCode.LOGIN_LOCKED, f"失败次数过多，请 {settings.STUDENT_LOCK_MINUTES} 分钟后重试")
        # 不区分账号是否存在（文档 3.1）
        raise_login_failure()

    if student.status != AccountStatus.ACTIVE:
        record_attempt(db, account_type="student", account=student_no, success=False, request=request, now=now)
        fail(ErrorCode.ACCOUNT_DISABLED)

    record_attempt(db, account_type="student", account=student_no, success=True, request=request, now=now)
    student.last_login_at = now
    access_token, expires_in, refresh_token = create_session(
        db, account_type="student", account_id=student.id, request=request
    )
    db.commit()
    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "is_first_login": student.is_first_login,
        "user": student,
    }


def raise_login_failure() -> None:
    fail(ErrorCode.BAD_CREDENTIALS, "学号或密码错误")


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
    """5 分钟有效、一次使用失效（文档 3.6）。"""
    if not captcha_id or not captcha_code:
        return False
    expected = cache.pop(f"captcha:{captcha_id}")
    if not expected:
        return False
    from app.core.security import constant_time_equals

    return constant_time_equals(captcha_code.strip().upper(), expected)


# ---------------------------------------------------------------- 管理员登录
def login_admin(db: Session, username: str, password: str, captcha_id: str, captcha_code: str, request: Request | None = None) -> dict:
    now = now_local()
    if not verify_captcha(captcha_id, captcha_code):
        fail(ErrorCode.PARAM_INVALID, "验证码错误或已失效", data={"field": "captcha_code", "reason": "验证码错误或已失效"})

    check_locked(db, "admin", username, settings.ADMIN_LOCK_MINUTES, now)

    admin = db.scalars(select(Admin).where(Admin.username == username)).first()
    if admin is None or not verify_password(password, admin.password_hash):
        count = record_attempt(db, account_type="admin", account=username, success=False, request=request, now=now)
        if count >= settings.LOGIN_MAX_FAILURES:
            fail(ErrorCode.LOGIN_LOCKED, f"失败次数过多，请 {settings.ADMIN_LOCK_MINUTES} 分钟后重试")
        fail(ErrorCode.BAD_CREDENTIALS, "账号或密码错误")

    if admin.status != AccountStatus.ACTIVE:
        record_attempt(db, account_type="admin", account=username, success=False, request=request, now=now)
        fail(ErrorCode.ACCOUNT_DISABLED)

    record_attempt(db, account_type="admin", account=username, success=True, request=request, now=now)
    admin.last_login_at = now
    access_token, expires_in, refresh_token = create_session(
        db, account_type="admin", account_id=admin.id, request=request, role=admin.role
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
def change_password(db: Session, account, *, account_type: str, old_password: str, new_password: str, keep_session_id: str | None) -> None:
    from app.core.security import password_strength_error

    now = now_local()
    if not verify_password(old_password, account.password_hash):
        fail(ErrorCode.PARAM_INVALID, "原密码不正确", data={"field": "old_password", "reason": "原密码不正确"})
    reference = getattr(account, "student_no", None) or getattr(account, "username", None)
    reason = password_strength_error(new_password, student_no=reference, old_password=old_password)
    if reason:
        fail(ErrorCode.PARAM_INVALID, reason, data={"field": "new_password", "reason": reason})

    account.password_hash = hash_password(new_password)
    account.is_first_login = False
    account.password_changed_at = now
    db.add(account)
    # 修改密码后其他会话立即失效（文档 2.2）
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


# 管理员权限点（文档 A-02）
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
