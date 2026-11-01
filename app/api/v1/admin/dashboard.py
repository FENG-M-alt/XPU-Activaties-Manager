"""工作台（A-03）。"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentAdmin, DbSession, activity_scope_conditions
from app.core.responses import ok
from app.services.stats import dashboard

router = APIRouter(tags=["管理端-工作台"])


@router.get("/dashboard", summary="A-03 工作台")
def get_dashboard(db: DbSession, admin: CurrentAdmin):
    """工作台指标、待办与近 30 天趋势；所有指标按当前管理员数据范围统计。"""
    data = dashboard(db, activity_scope_conditions(admin, db))
    return ok(data)
