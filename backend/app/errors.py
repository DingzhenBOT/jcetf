"""类型化错误层级（P0）。

对齐 fullstack-dev「每个错误都类型化、可记录、返回一致格式」：
  - 业务/可预期错误继承 AppError，携带 (code, status_code, message, details)。
  - 全局异常处理器（main.py）把 AppError 映射为统一 JSON；未预期异常记日志后返回 500。
  - 任何模块都应抛具体子类，禁止裸 `raise Exception("...")`。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AppError(Exception):
    """所有可预期错误的基类。"""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details: Dict[str, Any] = details or {}

    def to_dict(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            body["error"]["details"] = self.details
        if request_id is not None:
            body["request_id"] = request_id
        return body


class ConfigError(AppError):
    """配置缺失 / 非法（fail-fast 用）。"""

    code = "CONFIG_ERROR"
    status_code = 500


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404


class ValidationError(AppError):
    """输入 / 参数校验失败。"""

    code = "VALIDATION_ERROR"
    status_code = 422


class ConflictError(AppError):
    """资源冲突（如重复创建不可覆盖的版本）。"""

    code = "CONFLICT"
    status_code = 409


class DataSourceError(AppError):
    """数据源全部失败 / 不可用（502 交由上层决定是否降级）。"""

    code = "DATA_SOURCE_ERROR"
    status_code = 502


class UnavailableError(AppError):
    """服务暂不可用（如数据 STALE、意见生成暂停）。"""

    code = "SERVICE_UNAVAILABLE"
    status_code = 503
