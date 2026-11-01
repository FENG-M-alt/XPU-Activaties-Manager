"""应用入口：中间件、异常处理、路由挂载与后台状态推进任务。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import router as v1_router
from app.config import settings
from app.core.context import get_trace_id, new_trace_id, reset_trace_id, set_trace_id
from app.core.enums import AccountStatus
from app.core.errors import (
    BizError,
    DEFAULT_HTTP_STATUS,
    DEFAULT_MESSAGES,
    ErrorCode,
)
from app.core.responses import fail_envelope, ok
from app.database import SessionLocal, init_db

logger = logging.getLogger("lab_checkin")

API_PREFIX = settings.API_PREFIX


# ---------------------------------------------------------------- 后台任务
def _run_state_sync() -> int:
    from app.services.activity import sync_all_activities

    db = SessionLocal()
    try:
        return sync_all_activities(db)
    finally:
        db.close()


async def _state_task(stop: asyncio.Event) -> None:
    """每分钟推进一次活动与报名状态（文档 7.4）。"""
    from starlette.concurrency import run_in_threadpool

    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.STATE_TASK_INTERVAL)
            return
        except asyncio.TimeoutError:
            pass
        try:
            changed = await run_in_threadpool(_run_state_sync)
            if changed:
                logger.info("状态推进任务：%s 个活动状态已更新", changed)
        except Exception:  # pragma: no cover - 后台任务兜底
            logger.exception("状态推进任务执行失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()

    stop = asyncio.Event()
    task = None
    if settings.ENABLE_STATE_TASK:
        task = asyncio.create_task(_state_task(stop))
    logger.info("%s v%s 启动完成，接口前缀 %s", settings.APP_NAME, settings.APP_VERSION, API_PREFIX)
    try:
        yield
    finally:
        stop.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------- 应用
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "劳动活动签到系统后端 API（LAB-CHECKIN-API-001 V1.0）。\n\n"
            "- 统一响应信封：`{code, message, data, trace_id}`，`code=0` 表示成功\n"
            "- 认证：`Authorization: Bearer {access_token}`；refresh token 写入 HttpOnly Cookie\n"
            "- 时间：ISO 8601，带 `+08:00` 偏移\n"
            "- 管理端接口统一位于 `/api/v1/admin`"
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        incoming = request.headers.get("x-trace-id")
        trace_id = incoming or new_trace_id()
        token = set_trace_id(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            reset_trace_id(token)

    register_exception_handlers(app)

    app.include_router(v1_router, prefix=API_PREFIX)

    settings.ensure_dirs()
    app.mount(f"{API_PREFIX}/uploads", StaticFiles(directory=str(settings.COVER_DIR)), name="uploads")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs")

    @app.get(f"{API_PREFIX}/health", tags=["系统"], summary="健康检查")
    def health():
        from sqlalchemy import text

        from app.database import engine

        db_ok = True
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # pragma: no cover
            db_ok = False
        return ok(
            {
                "status": "ok" if db_ok else "degraded",
                "database": "ok" if db_ok else "error",
                "version": settings.APP_VERSION,
            }
        )

    @app.get(f"{API_PREFIX}/enums", tags=["系统"], summary="枚举字典（前端下拉用）")
    def enums():
        from app.core import enums as e

        return ok(
            {
                "activity_status": _enum_values(e.ActivityStatus),
                "registration_status": _enum_values(e.RegistrationStatus),
                "signup_state": _enum_values(e.SignupState),
                "checkin_method": _enum_values(e.CheckinMethod),
                "account_status": _enum_values(e.AccountStatus),
                "admin_role": _enum_values(e.AdminRole),
                "data_scope": _enum_values(e.DataScope),
                "import_result": _enum_values(e.ImportResult),
            }
        )

    return app


def _enum_values(enum_cls) -> list[str]:
    return [item.value for item in enum_cls]


# ---------------------------------------------------------------- 异常处理
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(
            status_code=exc.http_status,
            content=fail_envelope(exc.code, exc.message, exc.data),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path", "header")]
        field = ".".join(loc) or "request"
        reason = _humanize(first.get("msg", "参数校验失败"))
        return JSONResponse(
            status_code=400,
            content=fail_envelope(
                ErrorCode.PARAM_INVALID,
                f"参数校验失败：{reason}",
                {"field": field, "reason": reason, "errors": _brief_errors(errors)},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        mapping = {401: ErrorCode.UNAUTHORIZED, 403: ErrorCode.FORBIDDEN, 404: ErrorCode.NOT_FOUND}
        code = mapping.get(exc.status_code, ErrorCode.PARAM_INVALID)
        message = exc.detail if isinstance(exc.detail, str) else DEFAULT_MESSAGES.get(code, "请求失败")
        if exc.status_code == 405:
            message = "请求方法不被支持"
        return JSONResponse(status_code=exc.status_code, content=fail_envelope(code, message))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("未处理异常 trace_id=%s path=%s", get_trace_id(), request.url.path)
        return JSONResponse(
            status_code=500,
            content=fail_envelope(ErrorCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试"),
        )


def _humanize(msg: str) -> str:
    translations = {
        "Field required": "字段必填",
        "String should have at least": "长度不足",
        "String should have at most": "长度超出限制",
        "Input should be a valid integer": "须为整数",
        "Input should be a valid datetime": "时间格式不正确",
        "Input should be a valid date": "日期格式不正确",
        "Extra inputs are not permitted": "包含不被允许的字段",
        "Input should be greater than or equal to": "取值过小",
        "Input should be less than or equal to": "取值过大",
    }
    for key, value in translations.items():
        if key in msg:
            return value
    return msg


def _brief_errors(errors: list[dict]) -> list[dict]:
    return [
        {
            "field": ".".join(str(p) for p in e.get("loc", []) if p not in ("body", "query", "path", "header")),
            "reason": _humanize(e.get("msg", "")),
        }
        for e in errors[:10]
    ]


app = create_app()
