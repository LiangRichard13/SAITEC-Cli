"""Core 平台业务编排（L5）。

HTTP 细节在 core_api.CoreClient；本模块负责任务轮询、上传→检测/评测、
dataset 构造、模型 Key 脱敏等业务语义。CLI 命令只调用本模块。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

from ..core.utils import mask_secret
from ..core_api.client import CoreClient
from ..core_api.endpoints import (
    FILE_LIST,
    TASK_CANCEL,
    TASK_DETAIL,
    TASK_FILES,
    TASKS,
    TESTED_MODELS,
    skill_url,
)
from ..core_api.errors import CoreApiError, CoreApiErrorKind

StatusCallback = Callable[[dict[str, Any]], None]
_TERMINAL_SUCCESS = "succeeded"
_TERMINAL_FAILED = "failed"


class CoreOps:
    """Core 平台操作编排（异步）。"""

    def __init__(
        self,
        client: CoreClient,
        *,
        download_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.download_dir = Path(download_dir) if download_dir else None

    @staticmethod
    def _require_dict(
        data: Any, what: str, *, keys: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise CoreApiError(
                CoreApiErrorKind.SERVER,
                f"{what} 响应格式异常（期望 object）",
                detail=data,
            )
        missing = [key for key in keys if key not in data]
        if missing:
            raise CoreApiError(
                CoreApiErrorKind.SERVER,
                f"{what} 响应缺少字段: {missing}",
                detail=data,
            )
        return data

    # ---- 平台状态 / 模型 / 凭证 ----

    async def status(self) -> dict[str, Any]:
        """健康 + 鉴权 + 任务总数；不抛错，用字段表达失败。"""
        result: dict[str, Any] = {
            "base_url": self.client.base_url,
            "api_key_set": bool(self.client.api_key),
        }
        try:
            health = await self.client.health()
            result["health"] = health
            health_ok = isinstance(health, dict) and health.get("status") == "ok"
            reaper = health.get("jobs_reaper") if isinstance(health, dict) else None
            if (
                isinstance(reaper, dict)
                and reaper.get("enabled") is not False
                and reaper.get("alive") is False
            ):
                health_ok = False
            result["health_ok"] = health_ok
            if not health_ok:
                result["health_error"] = "health 响应格式异常或 jobs_reaper 未存活"
        except CoreApiError as exc:
            result["health_ok"] = False
            result["health_error"] = str(exc)

        if not self.client.api_key:
            result["auth_ok"] = False
            result["auth_error"] = "detector.api_key 未配置"
            return result
        try:
            data = await self.client.request_json(
                "GET", TASKS, params={"limit": 1}
            )
            data = self._require_dict(data, "任务列表", keys=("tasks",))
            result["auth_ok"] = True
            result["task_count"] = data.get("total")
        except CoreApiError as exc:
            result["auth_ok"] = False
            result["auth_error"] = str(exc)
            result["auth_error_kind"] = exc.kind.value
        return result

    async def list_models(self, *, skip: int = 0, limit: int = 100) -> dict[str, Any]:
        """列出被测模型与平台模型；两类模型的 api_key 都打码。"""
        data = await self.client.request_json(
            "GET", TESTED_MODELS, params={"skip": skip, "limit": limit}
        )
        data = self._require_dict(data, "被测模型列表", keys=("models",))
        for field in ("models", "platform_models"):
            entries = data.get(field)
            if not isinstance(entries, list):
                continue
            for model in entries:
                if not isinstance(model, dict):
                    continue
                key = model.get("api_key")
                if isinstance(key, str) and "***" not in key:
                    model["api_key"] = mask_secret(key)
        return data

    async def inject_credentials(
        self,
        skill: str,
        *,
        env_name: str,
        model_id: str,
        overwrite: bool = True,
    ) -> Any:
        data = await self.client.request_json(
            "POST",
            skill_url(skill, "credentials"),
            payload={"env_name": env_name, "model_id": model_id, "overwrite": overwrite},
        )
        return self._require_dict(data, "凭证响应", keys=("env_name",))

    # ---- 文件 ----

    async def upload_file(
        self, path: Path | str, file_type: str, *, timeout: float | None = None
    ) -> Any:
        data = await self.client.upload_file(path, file_type, timeout=timeout)
        return self._require_dict(data, "上传响应", keys=("file_id",))

    async def download_file(
        self,
        file_id: str,
        *,
        dest: Path | str | None = None,
        overwrite: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await self.client.download_file(
            file_id,
            dest=dest,
            dest_dir=self.download_dir,
            overwrite=overwrite,
            timeout=timeout,
        )

    async def list_files(self, *, skip: int = 0, limit: int = 100) -> Any:
        data = await self.client.request_json(
            "GET", FILE_LIST, params={"skip": skip, "limit": limit}
        )
        return self._require_dict(data, "文件列表", keys=("files",))

    async def read_file(self, file_id: str) -> Any:
        data = await self.client.read_file_content(file_id)
        return self._require_dict(data, "文件内容", keys=("content",))

    async def upload_dataset(
        self,
        path: Path | str,
        *,
        file_format: str | None = None,
        data_format: str = "case_list",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """上传 dataset 并构造 Core DatasetConfig；返回 (upload, dataset)。"""
        resolved_format = self._validate_dataset_format(
            path, file_format, data_format
        )
        upload = await self.upload_file(path, "dataset")
        if not isinstance(upload, dict):
            raise CoreApiError(
                CoreApiErrorKind.BUSINESS,
                "上传接口未返回文件元数据",
                detail=upload,
            )
        return upload, self.build_dataset(
            upload, file_format=resolved_format, data_format=data_format
        )

    @staticmethod
    def _validate_dataset_format(
        path: Path | str, file_format: str | None, data_format: str
    ) -> str:
        if data_format not in ("prompt_list", "case_list"):
            raise ValueError(
                f"dataset-data-format 必须是 prompt_list/case_list，当前为 {data_format!r}"
            )
        if file_format is not None:
            if file_format not in ("json", "jsonl"):
                raise ValueError(
                    f"dataset-file-format 必须是 json/jsonl，当前为 {file_format!r}"
                )
            return file_format
        suffix = Path(str(path)).suffix.lower()
        if suffix not in (".json", ".jsonl"):
            raise ValueError(
                "dataset 文件必须是 .json/.jsonl；其他格式请先转换，"
                "或用 --dataset-file-format 显式指定（仅 json/jsonl）"
            )
        return suffix[1:]

    @staticmethod
    def build_dataset(
        upload: dict[str, Any],
        *,
        file_format: str | None = None,
        data_format: str = "case_list",
    ) -> dict[str, Any]:
        storage_uri = upload.get("storage_uri")
        if not isinstance(storage_uri, str) or not storage_uri:
            raise CoreApiError(
                CoreApiErrorKind.BUSINESS,
                "上传接口未返回 storage_uri",
                detail=upload,
            )
        if data_format not in ("prompt_list", "case_list"):
            raise ValueError(
                f"dataset-data-format 必须是 prompt_list/case_list，当前为 {data_format!r}"
            )
        if file_format is None:
            suffix = Path(storage_uri).suffix.lower()
            if suffix not in (".json", ".jsonl"):
                raise ValueError(
                    "dataset storage_uri 必须是 .json/.jsonl；"
                    f"当前为 {storage_uri!r}"
                )
            file_format = suffix[1:]
        if file_format not in ("json", "jsonl"):
            raise ValueError(
                f"dataset-file-format 必须是 json/jsonl，当前为 {file_format!r}"
            )
        return {
            "source_type": "server_path",
            "path": storage_uri,
            "file_format": file_format,
            "data_format": data_format,
        }

    # ---- 任务 ----

    async def task_list(
        self,
        *,
        task_type: str | None = None,
        skip: int = 0,
        limit: int | None = 50,
        created_start: str | None = None,
        created_end: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"skip": skip}
        if limit is not None:
            params["limit"] = limit
        if task_type:
            params["task_type"] = task_type
        if created_start:
            params["created_start"] = created_start
        if created_end:
            params["created_end"] = created_end
        data = await self.client.request_json("GET", TASKS, params=params)
        return self._require_dict(data, "任务列表", keys=("tasks",))

    async def task_get(self, task_id: str) -> dict[str, Any]:
        data = await self.client.request_json(
            "GET", TASK_DETAIL.format(task_id=task_id)
        )
        if data is None:
            raise CoreApiError(
                CoreApiErrorKind.NOT_FOUND,
                f"任务不存在或无权访问: {task_id}",
                detail=data,
            )
        return self._require_dict(data, "任务详情", keys=("status",))

    async def task_artifacts(
        self,
        task_id: str,
        *,
        role: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Any:
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if role:
            params["role"] = role
        data = await self.client.request_json(
            "GET", TASK_FILES.format(task_id=task_id), params=params
        )
        return self._require_dict(data, "任务产物列表", keys=("files",))

    async def task_cancel(self, task_id: str) -> Any:
        data = await self.client.request_json(
            "POST", TASK_CANCEL.format(task_id=task_id)
        )
        if not isinstance(data, dict):
            raise CoreApiError(
                CoreApiErrorKind.BUSINESS,
                "取消接口未返回结果",
                detail=data,
            )
        return self._require_dict(data, "取消响应", keys=("cancelled",))

    async def task_wait(
        self,
        task_id: str,
        *,
        interval: float = 5.0,
        timeout: float = 3600.0,
        on_status: StatusCallback | None = None,
    ) -> dict[str, Any]:
        """轮询到终态；failed 抛 TASK_FAILED，超时抛 TIMEOUT。"""
        started = time.monotonic()
        delay = max(0.1, float(interval))
        last: dict[str, Any] | None = None
        while True:
            task = await self.task_get(task_id)
            last = task
            if on_status is not None:
                on_status(task)
            status = task.get("status")
            if status is None:
                raise CoreApiError(
                    CoreApiErrorKind.BUSINESS,
                    "任务详情缺少 status 字段",
                    detail=task,
                )
            if status == _TERMINAL_SUCCESS:
                return task
            if status == _TERMINAL_FAILED:
                message = str(task.get("error_message") or "任务执行失败")
                raise CoreApiError(
                    CoreApiErrorKind.TASK_FAILED, message, detail=task
                )
            if status != "running" and status not in ("pending", "queued"):
                raise CoreApiError(
                    CoreApiErrorKind.TASK_FAILED,
                    f"未知任务状态: {status!r}",
                    detail=task,
                )
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                raise CoreApiError(
                    CoreApiErrorKind.TIMEOUT,
                    f"任务 {task_id} 在 {timeout:.0f}s 内未完成"
                    f"（最后状态: {status or 'unknown'}）",
                    detail=last,
                )
            await asyncio.sleep(min(delay, max(0.0, timeout - elapsed)))

    # ---- 检测 / 评测提交 ----

    async def submit_detection(self, skill: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self.client.request_json(
            "POST", skill_url(skill, "detect"), payload=payload
        )
        return self._require_task(data)

    async def submit_batch_detection(
        self, skill: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        data = await self.client.request_json(
            "POST", skill_url(skill, "batch"), payload=payload
        )
        return self._require_task(data)

    async def submit_eval(self, skill: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self.client.request_json(
            "POST", skill_url(skill, "eval"), payload=payload
        )
        return self._require_task(data)

    @staticmethod
    def _require_task(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or not data.get("local_task_id"):
            raise CoreApiError(
                CoreApiErrorKind.BUSINESS,
                "后端未返回 local_task_id",
                detail=data,
            )
        result = dict(data)
        result["local_task_id"] = str(result["local_task_id"])
        return result
