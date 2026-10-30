"""v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1 import activities, auth, home, me, notices, registrations, share
from app.api.v1.admin import router as admin_router

router = APIRouter()

router.include_router(auth.router)
router.include_router(me.router)
router.include_router(home.router)
router.include_router(notices.router)
router.include_router(share.router)
router.include_router(activities.router)
router.include_router(registrations.router)
router.include_router(admin_router)
