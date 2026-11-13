"""Core 平台 API 客户端（L2）

只依赖 L1 core 与 aiohttp，不 import store/runtime/cli。
"""
from .client import CoreClient
from .errors import CoreApiError, CoreApiErrorKind

__all__ = ["CoreClient", "CoreApiError", "CoreApiErrorKind"]
