"""操作日志写入（文档 A-35）。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.timeutil import now_local
from app.models.audit import OperationLog


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


def write_log(
    db: Session,
    *,
    actor_type: str,
    actor_id: int | None,
    actor_name: str | None,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: Any = None,
    ip: str | None = None,
) -> OperationLog:
    """添加日志记录但不提交，由调用方统一提交。"""
    if detail is not None and not isinstance(detail, str):
        try:
            detail = json.dumps(detail, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            detail = str(detail)
    log = OperationLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip=ip,
        created_at=now_local(),
    )
    db.add(log)
    return log
