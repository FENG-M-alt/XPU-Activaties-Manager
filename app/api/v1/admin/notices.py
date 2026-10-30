"""管理端公告维护（扩展接口：文档未列管理端公告端点，学生端 U-17 需要数据来源）。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import CurrentAdmin, DbSession
from app.core.errors import ErrorCode, fail
from app.core.responses import ok
from app.core.timeutil import now_local
from app.models.notice import Notice
from app.schemas.requests import NoticeIn, NoticePatchIn
from app.schemas.serializers import notice_admin_item
from app.services.logs import client_ip, write_log

router = APIRouter(prefix="/notices", tags=["管理端-公告"])


@router.get("", summary="公告列表（管理端）")
def list_notices(db: DbSession, admin: CurrentAdmin):
    rows = db.scalars(
        select(Notice).where(Notice.is_deleted.is_(False)).order_by(Notice.created_at.desc())
    ).all()
    return ok({"items": [notice_admin_item(n) for n in rows], "total": len(rows)})


@router.post("", summary="新建公告（扩展）")
def create_notice(payload: NoticeIn, db: DbSession, admin: CurrentAdmin, request: Request):
    if payload.start_at and payload.end_at and payload.start_at >= payload.end_at:
        fail(ErrorCode.PARAM_INVALID, "失效时间须晚于生效时间",
             data={"field": "end_at", "reason": "须晚于生效时间"})
    notice = Notice(
        title=payload.title,
        content=payload.content,
        is_pinned=payload.is_pinned,
        start_at=payload.start_at,
        end_at=payload.end_at,
        is_active=payload.is_active,
        created_by=admin.id,
    )
    db.add(notice)
    db.flush()
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="NOTICE_CREATE",
        target_type="notice",
        target_id=notice.id,
        detail={"title": notice.title},
        ip=client_ip(request),
    )
    db.commit()
    return ok(notice_admin_item(notice), message="公告已创建")


@router.patch("/{notice_id}", summary="编辑公告（扩展）")
def update_notice(notice_id: int, payload: NoticePatchIn, db: DbSession, admin: CurrentAdmin, request: Request):
    notice = db.get(Notice, notice_id)
    if notice is None or notice.is_deleted:
        fail(ErrorCode.NOT_FOUND, "公告不存在")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(notice, field, value)
    if notice.start_at and notice.end_at and notice.start_at >= notice.end_at:
        fail(ErrorCode.PARAM_INVALID, "失效时间须晚于生效时间",
             data={"field": "end_at", "reason": "须晚于生效时间"})
    db.add(notice)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="NOTICE_UPDATE",
        target_type="notice",
        target_id=notice.id,
        detail={"fields": list(data.keys())},
        ip=client_ip(request),
    )
    db.commit()
    return ok(notice_admin_item(notice), message="保存成功")


@router.delete("/{notice_id}", summary="删除公告（扩展）")
def delete_notice(notice_id: int, db: DbSession, admin: CurrentAdmin, request: Request):
    notice = db.get(Notice, notice_id)
    if notice is None or notice.is_deleted:
        fail(ErrorCode.NOT_FOUND, "公告不存在")
    notice.is_deleted = True
    notice.is_active = False
    db.add(notice)
    write_log(
        db,
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        action="NOTICE_DELETE",
        target_type="notice",
        target_id=notice.id,
        ip=client_ip(request),
    )
    db.commit()
    return ok({"id": notice_id, "deleted": True}, message="公告已删除")
