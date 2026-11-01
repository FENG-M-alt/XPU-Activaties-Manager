"""数据库引擎与会话管理。

连接串由 DATABASE_URL 决定：
- 默认 SQLite（本地零配置开发）：      sqlite:///lab_checkin.db
- 生产按《数据库设计说明书 V1.0》：    mysql+pymysql://user:pass@host:3306/lab_checkin?charset=utf8mb4

MySQL 连接约定（文档 2.1 / 2.6）：
- 字符集 utf8mb4，引擎 InnoDB（由建表脚本保证）；
- 会话时区固定 +08:00，与"统一存东八区本地时间"一致；
- 建表优先执行 sql/01_schema_core.sql，create_all 仅作为开发期便利手段。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger("lab_checkin.db")


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict:
    kwargs: dict = {"echo": settings.SQL_ECHO, "pool_pre_ping": True, "future": True}
    if settings.is_sqlite:
        # SQLite：多线程共享连接，WAL 提升并发读
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        return kwargs

    if settings.is_mysql:
        # MySQL：utf8mb4 + 会话时区 +08:00 + 连接池
        kwargs["connect_args"] = {
            "charset": "utf8mb4",
            "init_command": "SET time_zone = '+08:00'",
            "connect_timeout": settings.DB_CONNECT_TIMEOUT,
        }
        kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=settings.DB_POOL_RECYCLE,
            pool_timeout=settings.DB_POOL_TIMEOUT,
        )
        return kwargs

    # PostgreSQL 等其他方言
    kwargs["connect_args"] = {"connect_timeout": settings.DB_CONNECT_TIMEOUT}
    kwargs.update(pool_size=settings.DB_POOL_SIZE, max_overflow=settings.DB_MAX_OVERFLOW)
    return kwargs


engine = create_engine(settings.DATABASE_URL, **_engine_kwargs())


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - 连接级设置
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session
)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def connection_info() -> dict:
    """当前连接信息（脱敏，用于 /health 与启动日志）。"""
    url = engine.url
    return {
        "dialect": url.get_backend_name(),
        "driver": url.get_driver_name(),
        "database": url.database,
        "host": url.host,
        "port": url.port,
    }


def check_connection() -> bool:
    """探测连接是否可用（不建表、不改数据）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - 连接异常
        logger.exception("数据库连接失败")
        return False


def init_db() -> None:
    """初始化数据库结构。

    AUTO_CREATE_TABLES=true 时按 ORM 模型建表（开发期便利）；
    生产环境应执行 sql/01_schema_core.sql + sql/02_schema_supplement.sql，
    并将 AUTO_CREATE_TABLES 设为 false。
    """
    settings.ensure_dirs()
    from app import models  # noqa: F401  确保所有模型已注册

    if not settings.AUTO_CREATE_TABLES:
        logger.info("AUTO_CREATE_TABLES=false，跳过建表（请使用 sql/ 下的建表脚本）")
        return
    Base.metadata.create_all(bind=engine)
