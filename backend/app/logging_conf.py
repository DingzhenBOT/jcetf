"""结构化日志（P0）。

对齐 fullstack-dev「结构化 JSON 日志 + request_id 贯穿」：
  - JSON 格式（带 ts/level/logger/msg/request_id + 任意 extra）。
  - 控制台（dev 可读、prod JSON 可配）+ 文件（TimedRotating 按日轮转，保留 N 天）。
  - request_id 经 contextvars 注入每条日志，便于按请求串联。
  - get_logger(name) 统一入口；setup_logging(settings) 在进程启动期调用一次。
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

from app.config import Settings

# request_id 在请求中间件里 bind，日志 filter 读取
_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName", "request_id",
}


def bind_request_id(rid: str):
    """在请求进入时绑定 request_id，返回 token 供 finally 清除。"""
    return _request_id.set(rid)


def clear_request_id(token) -> None:
    try:
        _request_id.reset(token)
    except (ValueError, LookupError):
        pass


def current_request_id() -> Optional[str]:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        # 合并业务 extra 字段
        for key, val in record.__dict__.items():
            if key in _RESERVED:
                continue
            payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None)
        prefix = f"[{rid}] " if rid else ""
        base = f"{datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()} {record.levelname:<7} {record.name}: {record.getMessage()}"
        out = prefix + base
        if record.exc_info:
            out += "\n" + self.formatException(record.exc_info)
        return out


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def _build_console_handler(level: str, json_console: bool) -> logging.Handler:
    handler: logging.Handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_console else HumanFormatter())
    handler.setLevel(level)
    handler.addFilter(RequestIdFilter())
    return handler


def _build_file_handler(settings: Settings) -> Optional[logging.Handler]:
    log_dir: Path = settings.paths.log_dir_abs
    if log_dir is None:
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    fh: logging.Handler = TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when=settings.logging.rotation_when,
        backupCount=settings.logging.rotation_backup_count,
        encoding="utf-8",
    )
    fh.setFormatter(JsonFormatter())
    fh.setLevel(settings.logging.level)
    fh.addFilter(RequestIdFilter())
    return fh


def setup_logging(settings: Settings) -> None:
    """进程启动期调用一次：配置 root logger（控制台 + 文件）。"""
    level = settings.logging.level.upper()
    root = logging.getLogger()
    root.setLevel(level)
    # 清掉重复 handler（reload / 测试场景）
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(_build_console_handler(level, settings.logging.json_console))
    fh = _build_file_handler(settings)
    if fh is not None:
        root.addHandler(fh)

    # 抑制第三方噪声，保留自身粒度
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
