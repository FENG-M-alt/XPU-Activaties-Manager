"""管理端路由包（/api/v1/admin）。"""
from fastapi import APIRouter

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

router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(activities.router)
router.include_router(registrations.router)
router.include_router(students.router)
router.include_router(stats.router)
router.include_router(exports.router)
router.include_router(notices.router)
router.include_router(admin_accounts.router)
router.include_router(uploads.router)
