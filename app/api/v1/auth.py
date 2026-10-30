"""认证与会话路由（U-01 ~ U-04）。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.core.deps import CurrentStudent, DbSession, OptionalPrincipal, PrincipalDep, get_optional_student
from app.core.errors import ErrorCode, fail
from app.core.guards import enforce_rate
from app.core.responses import ok
from app.schemas.requests import ChangePasswordIn, StudentLoginIn
from app.schemas.serializers import student_public
from app.services import auth as auth_service
from app.services.logs import client_ip, write_log

router = APIRouter(tags=["认证与会话"])


@router.post("/auth/login", summary="U-01 学生登录")
def student_login(payload: StudentLoginIn, request: Request, response: Response, db: DbSession):
    """账号密码登录，返回 access token 并通过 Set-Cookie 写入 refresh token。"""
    enforce_rate("login", client_ip(request) or "unknown", settings.RATE_LOGIN)

    result = auth_service.login_student(db, payload.student_no, payload.password, request)
    auth_service.set_refresh_cookie(response, result["refresh_token"])
    write_log(
        db,
        actor_type="student",
        actor_id=result["user"].id,
        actor_name=result["user"].name,
        action="LOGIN",
        target_type="student",
        target_id=result["user"].id,
        ip=client_ip(request),
    )
    db.commit()

    return ok(
        {
            "access_token": result["access_token"],
            "expires_in": result["expires_in"],
            "token_type": "Bearer",
            "user": student_public(result["user"]),
            "is_first_login": result["is_first_login"],
        }
    )


@router.post("/auth/refresh", summary="U-02 刷新令牌")
def refresh_token(request: Request, response: Response, db: DbSession):
    """凭 Cookie 中的 refresh token 换取新的 access token。"""
    refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    access_token, expires_in, _session = auth_service.refresh_access_token(db, refresh)
    db.commit()
    return ok({"access_token": access_token, "expires_in": expires_in, "token_type": "Bearer"})


@router.post("/auth/logout", summary="U-03 退出登录")
def logout(request: Request, response: Response, db: DbSession, principal: OptionalPrincipal):
    """使当前 access token 与 refresh token 同时失效并清除 Cookie。"""
    if principal is not None:
        auth_service.revoke_session_by_id(db, principal.session_id)
    refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if refresh:
        auth_service.revoke_session_by_refresh(db, refresh)
    db.commit()
    auth_service.clear_refresh_cookie(response)
    return ok(None)


@router.post("/auth/password", summary="U-04 修改密码")
def change_password(payload: ChangePasswordIn, principal: PrincipalDep, request: Request, response: Response, db: DbSession):
    """修改成功后注销其他会话（文档 2.2）。"""
    if principal.account_type != "student" or principal.student is None:
        fail(ErrorCode.FORBIDDEN)
    student = principal.student
    auth_service.change_password(
        db,
        student,
        account_type="student",
        old_password=payload.old_password,
        new_password=payload.new_password,
        keep_session_id=principal.session_id,
    )
    write_log(
        db,
        actor_type="student",
        actor_id=student.id,
        actor_name=student.name,
        action="CHANGE_PASSWORD",
        target_type="student",
        target_id=student.id,
        ip=client_ip(request),
    )
    db.commit()
    return ok({"is_first_login": False}, message="密码修改成功")


@router.get("/auth/check", summary="校验当前会话是否有效（辅助接口）")
def check_session(db: DbSession, request: Request):
    student = get_optional_student(db=db, authorization=request.headers.get("authorization"))
    if student is None:
        return ok({"authenticated": False})
    return ok({"authenticated": True, "student_no": student.student_no, "name": student.name})
