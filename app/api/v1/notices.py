"""公告列表（U-17）。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import or_, select

from app.core.deps import DbSession
from app.core.responses import ok
from app.core.timeutil import now_local
from app.models.supplement import Notice
from app.schemas.serializers import notice_out

router = APIRouter(tags=["用户端-公告"])


@router.get("/notices", summary="U-17 公告列表")
def list_notices(db: DbSession):
    """返回生效中的公告，含标题、内容、是否置顶、有效期；到期公告自动下线。"""
    now = now_local()
    rows = db.scalars(
        select(Notice)
        .where(
            Notice.is_active.is_(True),
            Notice.deleted_at.is_(None),
            or_(Notice.start_at.is_(None), Notice.start_at <= now),
            or_(Notice.end_at.is_(None), Notice.end_at >= now),
        )
        .order_by(Notice.is_pinned.desc(), Notice.created_at.desc())
    ).all()
    items = [notice_out(n) for n in rows if n.is_effective(now)]
    return ok({"items": items, "total": len(items)})
