"""Reporter — Layer 2

HTTP POST 批量上报到检测服务器，带 `X-API-Key` 认证。

错误分类（runtime 据此决策重试策略）：
- `AUTH`：401/403（X-API-Key 失效）→ 停止重试
- `PAYLOAD`：4xx 其他（请求体问题）→ 继续重试
- `SERVER`：5xx / 网络错误 / 超时 → 继续重试 + 指数退避
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aiohttp

from ..core.models import DetectionResult, DetectorConfig, Record

logger = logging.getLogger(__name__)

# 与 store 的 CHECK 约束对齐：域外 risk_level 会让整批入库失败并触发
# 无限重试死锁，解析时白名单兜底（服务端也应规范化，此处是最后防线）
_VALID_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})


def _normalize_risk(value: Any) -> str | None:
    """risk_level 白名单规范化：域外值 / 非字符串一律置 None"""
    if isinstance(value, str) and value.strip().lower() in _VALID_RISK_LEVELS:
        return value.strip().lower()
    return None


class ReportErrorKind(str, Enum):
    """上报错误类型（runtime 据此决策重试策略）"""

    AUTH = "AUTH"
    PAYLOAD = "PAYLOAD"
    SERVER = "SERVER"


class ReportError(Exception):
    """上报失败（含错误分类）"""

    def __init__(self, kind: ReportErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class Reporter:
    """HTTP 上报到检测服务器，带 `X-API-Key` 认证"""

    def __init__(
        self,
        config: DetectorConfig,
        client: aiohttp.ClientSession,
        timeout_sec: float | None = None,
    ) -> None:
        self._config = config
        self._client = client
        # 单批上报总超时：默认取 detector.timeout_sec（真实 LLM 检测慢，
        # 可在配置中调大；显式参数仍可覆盖，保留旧调用方式）
        self._timeout_sec = float(timeout_sec if timeout_sec is not None else config.timeout_sec)

    async def report(self, batch: list[Record]) -> list[DetectionResult]:
        """批量上报，返回检测结果

        检测服务器响应格式（约定，两种封装均兼容）：
        ```json
        // BaseResponse 包装（Core 服务端格式）
        {"success": true, "message": "success",
         "data": {"results": [{"record_id": "...", "detection_status": "clean", ...}]}}
        // 裸 results（mock detector 及旧版格式）
        {"results": [...]}
        ```

        Raises:
            ReportError: 上报失败（带 kind 分类）
        """
        if not batch:
            return []

        payload = {
            "batch": [dataclasses.asdict(r) for r in batch],
        }
        # url 只含 scheme+host+port；endpoint_path 默认 /detect（可配不同检测接口）
        url = self._config.url.rstrip("/") + self._config.endpoint_path
        headers = {"X-API-Key": self._config.api_key}

        try:
            async with self._client.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout_sec),
            ) as resp:
                if resp.status in (401, 403):
                    raise ReportError(
                        ReportErrorKind.AUTH,
                        f"auth failed ({resp.status}) at {url}; 检查 detector.api_key 是否与检测服务器一致，"
                        f"需要重设请用 `saitec-cli init --api-key ... --detector-url ... --force`",
                    )
                if 400 <= resp.status < 500:
                    body = await resp.text()
                    raise ReportError(
                        ReportErrorKind.PAYLOAD,
                        f"payload error ({resp.status}): {body[:200]}",
                    )
                if resp.status >= 500:
                    raise ReportError(
                        ReportErrorKind.SERVER,
                        f"server error ({resp.status})",
                    )
                data = await resp.json()
        except aiohttp.ClientError as e:
            raise ReportError(ReportErrorKind.SERVER, f"network error: {e}") from e
        except asyncio.TimeoutError as e:
            raise ReportError(
                ReportErrorKind.SERVER, f"timeout: {e}"
            ) from e

        return self._parse_response(batch, data)

    @staticmethod
    def _parse_response(
        batch: list[Record], data: dict[str, Any]
    ) -> list[DetectionResult]:
        """解析检测服务器响应，与 batch 关联成 DetectionResult

        兼容两种封装：优先取顶层 `results`（mock/旧版格式），缺失时解包
        BaseResponse 的 `data.results`（Core 服务端格式）。
        """
        if data.get("success") is False:
            logger.warning(
                "detector returned success=false with HTTP 200; treating as no results"
            )
        items = data.get("results")
        if items is None:
            inner = data.get("data")
            items = inner.get("results") if isinstance(inner, dict) else None
        if not isinstance(items, list):
            return []
        by_id = {r.record_id: r for r in batch}
        results: list[DetectionResult] = []
        for item in items:
            rid = item.get("record_id")
            if rid is None or rid not in by_id:
                continue
            r = by_id[rid]
            detected_at = item.get(
                "detected_at", datetime.now(timezone.utc).isoformat()
            )
            results.append(
                DetectionResult(
                    record_id=r.record_id,
                    service=r.service,
                    endpoint_type=r.endpoint_type,
                    upstream=r.upstream,
                    timestamp=r.timestamp,
                    status_code=r.status_code,
                    elapsed_ms=r.elapsed_ms,
                    model=r.request.get("model"),
                    prompt_tokens=(r.response.get("usage") or {}).get("prompt_tokens"),
                    completion_tokens=(r.response.get("usage") or {}).get(
                        "completion_tokens"
                    ),
                    finish_reason=r.response.get("finish_reason"),
                    error=r.error,
                    detection_status=item.get("detection_status", "clean"),
                    risk_level=_normalize_risk(item.get("risk_level")),
                    detection_detail=item.get("detection_detail"),
                    detected_at=detected_at,
                )
            )
        return results