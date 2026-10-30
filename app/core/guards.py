"""限流与幂等（文档 2.5 / 2.6）。

单进程内存实现；多实例部署请替换为 Redis 原子计数。
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from typing import Any

from app.config import settings
from app.core.cache import cache
from app.core.errors import ErrorCode, fail


# ---------------------------------------------------------------- 限流
class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.RLock()
        self._last_purge = 0.0

    def check(self, key: str, limit: int, window: int) -> bool:
        """返回 True 表示放行；False 表示超限。"""
        import time

        now = time.monotonic()
        with self._lock:
            if now - self._last_purge > 300:
                self._purge(now)
                self._last_purge = now
            bucket = self._hits.setdefault(key, deque())
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def _purge(self, now: float) -> None:
        empty = [k for k, v in self._hits.items() if not v or now - v[-1] > 3600]
        for k in empty:
            self._hits.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def parse_rate(rate: str, default_limit: int, default_window: int) -> tuple[int, int]:
    try:
        limit_part, _, window_part = rate.partition("/")
        return int(limit_part), int(window_part)
    except (ValueError, AttributeError):
        return default_limit, default_window


def enforce_rate(kind: str, subject: str, rate: str) -> None:
    """按配置限流，超限抛 42900。"""
    if not settings.RATE_LIMIT_ENABLED:
        return
    limit, window = parse_rate(rate, 30, 60)
    if not limiter.check(f"{kind}:{subject}", limit, window):
        fail(ErrorCode.TOO_MANY_REQUESTS)


# ---------------------------------------------------------------- 幂等
def idempotency_key(account_type: str, subject: int, path: str, body: Any) -> str:
    """幂等键 = user_id + path + body（文档 2.5.2）。"""
    try:
        body_repr = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        body_repr = str(body)
    digest = hashlib.sha256(f"{account_type}:{subject}:{path}:{body_repr}".encode("utf-8")).hexdigest()
    return f"idem:{digest}"


def replay(key: str) -> tuple[bool, Any]:
    """返回 (是否命中, 首次结果)。"""
    hit = cache.get(key)
    if hit is None:
        return False, None
    return True, hit


def remember(key: str, payload: Any) -> None:
    cache.set(key, payload, settings.IDEMPOTENCY_WINDOW)


def clear_idempotency() -> None:
    cache.clear()
