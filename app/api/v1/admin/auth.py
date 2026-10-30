"""管理端认证（A-01 / A-02）与验证码。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession, OptionalPrincipal, PrincipalDep
from app.core.errors import ErrorCode, fail
from app.core.guards import enforce_rate
from app.core.responses import ok
from app.schemas.requests import AdminLoginIn, ChangePasswordIn
from app.schemas.serializers import admin_profile
from app.services import auth as auth_service
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["管理端-认证"])


@router.get("/auth/captcha", summary="获取图形验证码（A-01 配套）")
def get_captcha():
    """返回验证码标识与 SVG 图片（data URL），5 分钟有效、一次使用失效。"""
    return ok(auth_service.issue_captcha())


@router.post("/auth/login", summary="A-01 管理员登录")
def admin_login(payload: AdminLoginIn, request: Request, response: Response, db: DbSession):
    """账号、密码、图形验证码登录；连续 5 次失败锁定 15 分钟。"""
    enforce_rate("admin_login", client_ip(request) or "unknown", settings.RATE_LOGIN)

    result = auth_service.login_admin(
        db,
        payload.username,
        payload.password,
        payload.captcha_id,
        payload.captcha_code,
        request,
    )
    auth_service.set_refresh_cookie(response, result["refresh_token"])
    admin = result["user"]
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="LOGIN",
        target_type="admin",
        target_id=admin.id,
        ip=client_ip(request),
    )
    db.commit()

    return ok(
        {
            "access_token": result["access_token"],
            "expires_in": result["expires_in"],
            "token_type": "Bearer",
            "user": {
                **admin_profile(admin, auth_service.permissions_for(admin)),
                "is_first_login": admin.is_first_login,
            },
            "is_first_login": result["is_first_login"],
        }
    )


@router.post("/auth/refresh", summary="刷新管理员令牌")
def admin_refresh(request: Request, db: DbSession):
    refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    access_token, expires_in, session = auth_service.refresh_access_token(db, refresh)
    if session.account_type != "admin":
        fail(ErrorCode.FORBIDDEN)
    db.commit()
    return ok({"access_token": access_token, "expires_in": expires_in, "token_type": "Bearer"})


@router.post("/auth/logout", summary="管理员退出登录")
def admin_logout(request: Request, response: Response, db: DbSession, principal: OptionalPrincipal):
    if principal is not None:
        auth_service.revoke_session_by_id(db, principal.session_id)
    refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if refresh:
        auth_service.revoke_session_by_refresh(db, refresh)
    db.commit()
    auth_service.clear_refresh_cookie(response)
    return ok(None)


@router.post("/auth/password", summary="管理员修改密码（扩展）")
def admin_change_password(payload: ChangePasswordIn, principal: PrincipalDep, db: DbSession):
    if principal.account_type != "admin" or principal.admin is None:
        fail(ErrorCode.FORBIDDEN)
    auth_service.change_password(
        db,
        principal.admin,
        account_type="admin",
        old_password=payload.old_password,
        new_password=payload.new_password,
        keep_session_id=principal.session_id,
    )
    return ok(None, message="密码修改成功")


@router.get("/me", summary="A-02 当前管理员")
def get_admin_me(admin: CurrentAdmin):
    """返回管理员信息与权限点列表，供前端生成菜单与按钮权限。"""
    return ok(admin_profile(admin, auth_service.permissions_for(admin)))
