"""操作日志写入（文档 4.7 / A-35）。

表结构：operation_logs(operator_type, operator_id, action, target_type, target_id,
detail JSON, ip, ua, result)。detail 直接存 JSON，便于按对象追溯变更前后值。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.enums import LogOperatorType, LogResult
from app.core.timeutil import now_local
from app.models.operation_log import OperationLog


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


def client_ua(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:255] if ua else None


_OPERATOR_MAP = {
    "student": LogOperatorType.STUDENT,
    "STUDENT": LogOperatorType.STUDENT,
    "user": LogOperatorType.STUDENT,
    "USER": LogOperatorType.STUDENT,
    "admin": LogOperatorType.ADMIN,
    "ADMIN": LogOperatorType.ADMIN,
    "system": LogOperatorType.SYSTEM,
    "SYSTEM": LogOperatorType.SYSTEM,
}


def _normalize_operator_type(value: str | None) -> str:
    return _OPERATOR_MAP.get(value or "", LogOperatorType.SYSTEM)


def write_log(
    db: Session,
    *,
    action: str,
    operator_type: str | None = None,
    operator_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: Any = None,
    ip: str | None = None,
    ua: str | None = None,
    result: str = LogResult.SUCCESS,
    # 兼容旧调用签名
    actor_type: str | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> OperationLog:
    """添加日志记录但不提交，由调用方统一提交。

    actor_* 为旧参数名，内部统一映射为 operator_*；actor_name 附加到 detail 中
    （operation_logs 表没有姓名字段，姓名可由 operator_id 关联查出）。
    """
    op_type = operator_type or actor_type
    op_id = operator_id if operator_id is not None else actor_id

    payload: Any = detail
    if actor_name:
        if isinstance(detail, dict):
            payload = {**detail, "operator_name": actor_name}
        elif detail is None:
            payload = {"operator_name": actor_name}
        else:
            payload = {"summary": detail, "operator_name": actor_name}

    log = OperationLog(
        operator_type=_normalize_operator_type(op_type),
        operator_id=op_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=payload,
        ip=ip,
        ua=ua,
        result=result or LogResult.SUCCESS,
        created_at=now_local(),
    )
    db.add(log)
    return log
