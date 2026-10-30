"""请求级上下文：trace_id（文档 2.9）。"""
from __future__ import annotations

import contextvars
import secrets

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """16 位十六进制链路标识，与响应体 trace_id 一致。"""
    return secrets.token_hex(8)


def set_trace_id(trace_id: str) -> contextvars.Token:
    return _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    value = _trace_id_var.get()
    return value if value else new_trace_id()


def reset_trace_id(token: contextvars.Token) -> None:
    try:
        _trace_id_var.reset(token)
    except (ValueError, RuntimeError):  # pragma: no cover - 跨上下文重置
        pass
