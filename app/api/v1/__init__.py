"""v1 路由聚合。"""
from fastapi import APIRouter, Depends

from app.api.v1 import activities, auth, home, me, notices, registrations, share
from app.core.deps import require_password_change_done
from app.api.v1.admin import router as admin_router

router = APIRouter()

router.include_router(auth.router)
router.include_router(me.router)
_NEED_CHANGE = [Depends(require_password_change_done)]
router.include_router(home.router, dependencies=_NEED_CHANGE)
router.include_router(notices.router, dependencies=_NEED_CHANGE)
router.include_router(share.router)
router.include_router(activities.router, dependencies=_NEED_CHANGE)
router.include_router(registrations.router, dependencies=_NEED_CHANGE)
router.include_router(admin_router)
