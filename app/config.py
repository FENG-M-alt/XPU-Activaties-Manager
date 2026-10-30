"""应用配置。

所有配置项均可通过环境变量或 .env 文件覆盖（见 .env.example）。
"""
from __future__ import annotations

import os
from datetime import timezone, timedelta
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 服务端统一按东八区处理（文档 2.1 / 7.5）
LOCAL_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _load_dotenv() -> None:
    """极简 .env 读取，避免额外依赖。已存在的环境变量优先。"""
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default))
    return p if p.is_absolute() else (BASE_DIR / p)


class Settings:
    """运行时配置。"""

    # --- 基础 ---
    APP_NAME: str = os.environ.get("APP_NAME", "劳动活动签到系统 后端 API")
    APP_VERSION: str = os.environ.get("APP_VERSION", "1.0.0")
    API_PREFIX: str = os.environ.get("API_PREFIX", "/api/v1")
    DEBUG: bool = _env_bool("DEBUG", True)

    # --- 数据库 ---
    DATABASE_URL: str = os.environ.get("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'lab_checkin.db').as_posix()}")
    SQL_ECHO: bool = _env_bool("SQL_ECHO", False)

    # --- 认证（文档 2.2） ---
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-only-secret-change-me-in-production")
    JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_TTL: int = _env_int("ACCESS_TOKEN_TTL", 2 * 60 * 60)          # 2 小时
    REFRESH_TOKEN_TTL: int = _env_int("REFRESH_TOKEN_TTL", 7 * 24 * 60 * 60)   # 7 天
    MAX_SESSIONS_PER_ACCOUNT: int = _env_int("MAX_SESSIONS_PER_ACCOUNT", 3)
    REFRESH_COOKIE_NAME: str = os.environ.get("REFRESH_COOKIE_NAME", "refresh_token")
    COOKIE_SECURE: bool = _env_bool("COOKIE_SECURE", False)       # 生产环境应置为 True
    COOKIE_SAMESITE: str = os.environ.get("COOKIE_SAMESITE", "lax")
    COOKIE_DOMAIN: str | None = os.environ.get("COOKIE_DOMAIN") or None
    COOKIE_PATH: str = os.environ.get("COOKIE_PATH", "/")

    # --- 安全策略 ---
    LOGIN_MAX_FAILURES: int = _env_int("LOGIN_MAX_FAILURES", 5)
    STUDENT_LOCK_MINUTES: int = _env_int("STUDENT_LOCK_MINUTES", 10)
    ADMIN_LOCK_MINUTES: int = _env_int("ADMIN_LOCK_MINUTES", 15)
    CHECKIN_MAX_FAILURES: int = _env_int("CHECKIN_MAX_FAILURES", 5)
    CHECKIN_LOCK_MINUTES: int = _env_int("CHECKIN_LOCK_MINUTES", 10)
    CAPTCHA_TTL: int = _env_int("CAPTCHA_TTL", 5 * 60)            # 5 分钟
    IDEMPOTENCY_WINDOW: int = _env_int("IDEMPOTENCY_WINDOW", 5)   # 5 秒

    # --- 限流（文档 2.6） ---
    RATE_LIMIT_ENABLED: bool = _env_bool("RATE_LIMIT_ENABLED", True)
    RATE_LOGIN: str = os.environ.get("RATE_LOGIN", "10/60")        # 次数/秒
    RATE_CHECKIN: str = os.environ.get("RATE_CHECKIN", "10/60")
    RATE_EXPORT: str = os.environ.get("RATE_EXPORT", "5/3600")
    RATE_WRITE: str = os.environ.get("RATE_WRITE", "30/60")

    # --- 文件 ---
    UPLOAD_DIR: Path = _env_path("UPLOAD_DIR", "storage/uploads")
    EXPORT_DIR: Path = _env_path("EXPORT_DIR", "storage/exports")
    COVER_DIR: Path = _env_path("COVER_DIR", "storage/covers")
    MAX_IMPORT_SIZE: int = _env_int("MAX_IMPORT_SIZE", 10 * 1024 * 1024)  # 10MB
    MAX_IMPORT_ROWS: int = _env_int("MAX_IMPORT_ROWS", 5000)
    MAX_COVER_SIZE: int = _env_int("MAX_COVER_SIZE", 2 * 1024 * 1024)     # 2MB
    IMPORT_BATCH_TTL: int = _env_int("IMPORT_BATCH_TTL", 30 * 60)        # 30 分钟
    EXPORT_FILE_TTL_DAYS: int = _env_int("EXPORT_FILE_TTL_DAYS", 7)
    EXPORT_SYNC_LIMIT: int = _env_int("EXPORT_SYNC_LIMIT", 10000)
    DOWNLOAD_TOKEN_TTL: int = _env_int("DOWNLOAD_TOKEN_TTL", 30 * 60)

    # --- 业务默认值 ---
    DEFAULT_PAGE_SIZE_STUDENT: int = 10
    DEFAULT_PAGE_SIZE_ADMIN: int = 20
    MAX_PAGE_SIZE: int = 100
    MAKEUP_CHECKIN_DAYS: int = _env_int("MAKEUP_CHECKIN_DAYS", 7)

    # --- 启动行为 ---
    AUTO_SEED: bool = _env_bool("AUTO_SEED", True)
    ENABLE_STATE_TASK: bool = _env_bool("ENABLE_STATE_TASK", True)
    STATE_TASK_INTERVAL: int = _env_int("STATE_TASK_INTERVAL", 60)
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # --- 跨域（前后端分离联调；生产请改为具体域名） ---
    CORS_ORIGINS: str = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8080,http://127.0.0.1:8080",
    )

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    def ensure_dirs(self) -> None:
        for d in (self.UPLOAD_DIR, self.EXPORT_DIR, self.COVER_DIR):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
