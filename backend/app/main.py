"""FastAPI 入口（etf-api 进程，1 worker，无鉴权层）。

对齐 DESIGN §0 / §7：鉴权全在 Nginx（Basic Auth + HTTPS），本进程不实现鉴权。
fullstack-dev 落实项：/health + /ready、全局异常处理器、显式 CORS、安全头、request_id 中间件、lifespan 优雅关闭。
P0 仅含骨架端点；业务路由在 P4+ 挂载。
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.session import ping_db
from app.errors import AppError, ConfigError
from app.logging_conf import clear_request_id, get_logger, setup_logging

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_id(request: Request) -> Optional[str]:
    return request.headers.get("x-request-id") or request.state.request_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：加载配置（fail-fast）。配置异常会让 uvicorn 启动失败。
    settings = get_settings()
    setup_logging(settings)
    settings.ensure_dirs()
    logger.info(
        "etf-api lifespan start",
        extra={"environment": settings.app.environment, "host": settings.server.host, "port": settings.server.port},
    )
    try:
        yield
    finally:
        # P0 无长连接资源；P1+ 在此关闭 DB 引擎 / 连接池。
        logger.info("etf-api lifespan shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ETF Monitor API",
        version="0.1.0",
        description="A股板块资金监控与 ETF 辅助分析（MVP，无鉴权层，由 Nginx 保护）",
        lifespan=lifespan,
    )

    # ---- CORS：显式白名单，绝不 "*" ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allowed_origins,
        allow_methods=settings.cors.allowed_methods,
        allow_headers=["*"],
        allow_credentials=settings.cors.allow_credentials,
    )

    # ---- request_id 中间件（贯穿日志） ----
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        token = None
        try:
            from app.logging_conf import bind_request_id

            token = bind_request_id(rid)
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            return response
        finally:
            if token is not None:
                clear_request_id(token)

    # ---- 安全响应头（Nginx 也可统一设，此处兜底） ----
    if settings.security.enable_headers:

        @app.middleware("http")
        async def security_headers_middleware(request: Request, call_next):
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault(
                "Content-Security-Policy", settings.security.content_security_policy
            )
            return response

    # ---- 全局异常处理器：AppError -> 一致 JSON；未预期 -> 500 ----
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        logger.warning(
            "app error",
            extra={"code": exc.code, "status": exc.status_code, "path": request.url.path},
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict(_request_id(request)))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "VALIDATION_ERROR", "message": "input validation failed", "details": exc.errors()},
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        logger.error("unexpected error", exc_info=exc, extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "INTERNAL_ERROR", "message": "internal server error"},
                "request_id": _request_id(request),
            },
        )

    # ---- 系统端点 ----
    @app.get("/health", tags=["system"])
    async def health():
        # liveness：进程活着即可，不依赖外部资源
        return {"status": "ok", "service": "etf-api", "timestamp": _now_iso()}

    @app.get("/ready", tags=["system"])
    async def ready():
        # readiness：P0 检查配置已加载 + 数据目录可写；P1 增加 DB ping
        s = get_settings()
        s.ensure_dirs()
        writable = _dir_writable(s.paths.data_dir_abs)
        db_ok = ping_db(s)
        checks = {
            "config": "ok",
            "data_dir_writable": "ok" if writable else "fail",
            "db": "ok" if db_ok else "fail",
        }
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ok" if ok else "degraded",
                "checks": checks,
                "timestamp": _now_iso(),
            },
        )

    return app


def _dir_writable(p) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# 模块导入即构建 app（uvicorn app.main:app 直接引用）
try:
    app = create_app()
except ConfigError as e:
    # fail-fast：配置错误时打印并让进程以错误退出
    logging.getLogger("etf-monitor.boot").error("config load failed: %s", e)
    raise
