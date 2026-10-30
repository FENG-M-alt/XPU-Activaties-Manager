"""进程内 TTL 缓存：验证码、幂等键、登录/签到锁定计数。

单进程内存实现，足以支撑开发与单实例部署；多实例部署时应替换为 Redis
（接口保持不变，见下方 TTLStore 的 get/set/pop/incr 语义）。
"""
from __future__ import annotations

import threading
import time
from typing import Any


class TTLStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def _purge(self, now: float) -> None:
        expired = [k for k, (exp, _) in self._data.items() if exp <= now]
        for k in expired:
            self._data.pop(k, None)

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            now = time.monotonic()
            item = self._data.get(key)
            if item is None:
                return default
            exp, value = item
            if exp <= now:
                self._data.pop(key, None)
                return default
            return value

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._data.pop(key, None)
            if item is None:
                return default
            exp, value = item
            return value if exp > time.monotonic() else default

    def exists(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ttl: float) -> int:
        """计数自增；首次写入时设置过期时间。"""
        with self._lock:
            now = time.monotonic()
            item = self._data.get(key)
            if item is None or item[0] <= now:
                self._data[key] = (now + ttl, 1)
                return 1
            exp, value = item
            value = int(value) + 1
            self._data[key] = (exp, value)
            return value

    def ttl(self, key: str) -> float:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return 0.0
            remaining = item[0] - time.monotonic()
            return max(remaining, 0.0)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def purge(self) -> None:
        with self._lock:
            self._purge(time.monotonic())


_MISSING = object()

cache = TTLStore()
