"""Core API 错误类型与分类。"""
from __future__ import annotations

from enum import Enum
from typing import Any


class CoreApiErrorKind(str, Enum):
    """错误类别；CLI 层据此映射退出码与提示。"""

    CONFIG = "CONFIG"
    AUTH = "AUTH"
    BUSINESS = "BUSINESS"
    PAYLOAD = "PAYLOAD"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER = "SERVER"
    NETWORK = "NETWORK"
    IO = "IO"
    TASK_FAILED = "TASK_FAILED"
    TIMEOUT = "TIMEOUT"


class CoreApiError(Exception):
    """Core API 调用失败。

    Attributes:
        kind: 错误类别。
        status: HTTP 状态码（网络/IO 错误为 None）。
        detail: 后端响应体（已解析的 JSON 或文本），便于排错；不包含请求头。
        retry_after: 429/503 响应头中的 Retry-After。
    """

    def __init__(
        self,
        kind: CoreApiErrorKind,
        message: str,
        *,
        status: int | None = None,
        detail: Any = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.detail = detail
        self.retry_after = retry_after
