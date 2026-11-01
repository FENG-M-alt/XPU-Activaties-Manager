"""分享页（U-10）：免登录，不返回报名名单、签到码与他人信息。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import DbSession
from app.core.enums import ActivityStatus
from app.core.errors import ErrorCode, fail
from app.core.responses import ok
from app.core.timeutil import now_local
from app.models.activity import Activity
from app.schemas.serializers import activity_list_item, api_base
from app.services.activity import remaining_map

router = APIRouter(tags=["用户端-分享"])


@router.get("/share/{token}", summary="U-10 分享链接详情")
def share_detail(token: str, db: DbSession, request: Request):
    """token 为 12 位随机字符；链接失效返回 40400，活动取消返回 CANCELLED。"""
    now = now_local()
    activity = db.scalars(
        select(Activity).where(Activity.share_token == token, Activity.deleted_at.is_(None))
    ).first()
    if activity is None:
        fail(ErrorCode.NOT_FOUND, "分享链接不存在或已失效")
    if activity.share_expire_at and now > activity.share_expire_at:
        fail(ErrorCode.NOT_FOUND, "分享链接已过期")

    counts = remaining_map(db, [activity.id])
    data = activity_list_item(
        activity,
        now=now,
        remaining=max(activity.capacity - counts.get(activity.id, 0), 0),
        registered_count=counts.get(activity.id, 0),
    )
    data.update(
        {
            "description": activity.description,
            "notice": activity.notice,
            "organizer": activity.organizer,
            "share_url": f"{api_base(request)}/share/{token}",
        }
    )
    message = "ok"
    if activity.status == ActivityStatus.CANCELLED:
        data["status"] = ActivityStatus.CANCELLED
        message = "活动已取消"
    return ok(data, message=message)
