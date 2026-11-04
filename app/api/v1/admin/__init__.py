"""管理端路由包（/api/v1/admin）。"""
from fastapi import APIRouter, Depends

from app.core.deps import CurrentAdmin, require_admin_password_change_done
from app.core.errors import ErrorCode, fail

from app.api.v1.admin import (
    activities,
    admin_accounts,
    auth,
    dashboard,
    exports,
    notices,
    registrations,
    stats,
    students,
    uploads,
)

router = APIRouter(prefix="/admin")

# auth 路由（登录/刷新/退出/改密/当前管理员/验证码）始终放行，避免首登用户被锁死
router.include_router(auth.router)

_NEED_CHANGE = [Depends(require_admin_password_change_done)]
router.include_router(dashboard.router, dependencies=_NEED_CHANGE)
router.include_router(activities.router, dependencies=_NEED_CHANGE)
router.include_router(registrations.router, dependencies=_NEED_CHANGE)
router.include_router(students.router, dependencies=_NEED_CHANGE)
router.include_router(stats.router, dependencies=_NEED_CHANGE)
router.include_router(exports.router, dependencies=_NEED_CHANGE)
router.include_router(notices.router, dependencies=_NEED_CHANGE)
router.include_router(admin_accounts.router, dependencies=_NEED_CHANGE)
router.include_router(uploads.router, dependencies=_NEED_CHANGE)


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
def admin_path_not_found(path: str, _admin: CurrentAdmin):
    """兜底路由：未定义的管理端路径也先过鉴权。

    计划的 Top-5 第 2 条要求"学生 token 打未定义的管理端路径也必须 403"；
    若没有兜底，路由匹配失败会直接 404，绕过角色校验并暴露路径是否存在。
    这里依赖 CurrentAdmin，学生 token → 40300，管理员 → 40400。
    """
    fail(ErrorCode.NOT_FOUND, "接口不存在")
