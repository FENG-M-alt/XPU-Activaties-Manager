"""统一响应信封（文档 2.3）。"""
from __future__ import annotations

from typing import Any

from app.core.context import get_trace_id


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应信封：code=0。"""
    return {"code": 0, "message": message, "data": data, "trace_id": get_trace_id()}


def fail_envelope(code: int, message: str, data: Any = None, trace_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id or get_trace_id()}


def page_data(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    """分页响应结构（文档 2.4）。"""
    return {"items": items, "total": total, "page": page, "page_size": page_size}


class Pagination:
    """解析并裁剪分页参数：page>=1，1<=page_size<=100。"""

    __slots__ = ("page", "page_size", "clamped", "offset")

    def __init__(self, page: int | None, page_size: int | None, default_size: int = 20, max_size: int = 100):
        self.page = max(int(page or 1), 1)
        raw_size = int(page_size or default_size)
        self.clamped = raw_size > max_size
        self.page_size = min(max(raw_size, 1), max_size)
        self.offset = (self.page - 1) * self.page_size

    def message(self, base: str = "ok") -> str:
        if self.clamped:
            return f"{base}（page_size 超过 100，已按 100 处理）"
        return base
