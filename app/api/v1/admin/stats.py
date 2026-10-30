"""管理端统计（A-28 ~ A-31）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query

from app.config import settings
from app.core.deps import CurrentAdmin, DbSession, activity_scope_conditions
from app.core.responses import Pagination, ok, page_data
from app.core.timeutil import day_end, day_start, now_local, term_bounds
from app.services import stats as stats_service

router = APIRouter(tags=["管理端-统计"])

RANGE_PRESETS = {"7d": 7, "30d": 30, "90d": 90, "180d": 180}


def _range_bounds(range_: str | None, start_date: date | None, end_date: date | None, now: datetime):
    if start_date or end_date:
        return (day_start(start_date) if start_date else None, day_end(end_date) if end_date else None)
    key = (range_ or "30d").lower()
    if key in RANGE_PRESETS:
        return day_start(now) - timedelta(days=RANGE_PRESETS[key] - 1), day_end(now)
    if key in ("term", "this_term"):
        return term_bounds(now)
    if key in ("all", "none", ""):
        return None, None
    return day_start(now) - timedelta(days=29), day_end(now)


@router.get("/stats/overview", summary="A-28 数据总览")
def stats_overview(
    db: DbSession,
    admin: CurrentAdmin,
    range: str | None = Query("30d", description="7d / 30d / 90d / term / all，或配合日期"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    college: str | None = Query(None),
    type_id: int | None = Query(None),
):
    """返回活动数、报名人次、签到人次、签到率、参与人数（去重）、累计学时，以及趋势、类型分布与学院排行。"""
    now = now_local()
    start, end = _range_bounds(range, start_date, end_date, now)
    data = stats_service.overview(
        db,
        activity_scope_conditions(admin),
        range_start=start,
        range_end=end,
        college=college,
        type_id=type_id,
        now=now,
    )
    data["range"] = {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None}
    return ok(data)


@router.get("/stats/activities", summary="A-29 活动维度统计")
def stats_activities(
    db: DbSession,
    admin: CurrentAdmin,
    keyword: str | None = Query(None),
    type_id: int | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """按活动聚合：报名人数、通过人数、签到人数、缺席人数、签到率、学时合计。"""
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    items, total = stats_service.stats_by_activity(
        db,
        activity_scope_conditions(admin),
        page_offset=pagination.offset,
        limit=pagination.page_size,
        keyword=keyword,
        type_id=type_id,
        range_start=day_start(start_date) if start_date else None,
        range_end=day_end(end_date) if end_date else None,
    )
    return ok(page_data(items, total, pagination.page, pagination.page_size))


@router.get("/stats/students", summary="A-30 学生维度统计")
def stats_students(
    db: DbSession,
    admin: CurrentAdmin,
    keyword: str | None = Query(None),
    college: str | None = Query(None),
    grade: str | None = Query(None),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    """按学生聚合：参与次数、累计学时、最近参与时间。"""
    pagination = Pagination(page, page_size, default_size=settings.DEFAULT_PAGE_SIZE_ADMIN)
    items, total = stats_service.stats_by_student(
        db,
        activity_scope_conditions(admin),
        page_offset=pagination.offset,
        limit=pagination.page_size,
        keyword=keyword,
        college=college,
        grade=grade,
    )
    return ok(page_data(items, total, pagination.page, pagination.page_size))


@router.get("/stats/colleges", summary="A-31 学院维度统计")
def stats_colleges(
    db: DbSession,
    admin: CurrentAdmin,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
):
    """按学院聚合：在册学生数、参与人数、参与率、活动数、人均学时、签到率。"""
    items = stats_service.stats_by_college(
        db,
        activity_scope_conditions(admin),
        range_start=day_start(start_date) if start_date else None,
        range_end=day_end(end_date) if end_date else None,
    )
    return ok({"items": items, "total": len(items)})
