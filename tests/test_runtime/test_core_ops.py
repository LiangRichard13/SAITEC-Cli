"""CoreOps 编排测试：fake CoreClient，不跑真实网络。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from saitec_cli.core_api.errors import CoreApiError, CoreApiErrorKind
from saitec_cli.runtime.core_ops import CoreOps


class FakeClient:
    base_url = "http://core:8080"
    api_key = "test-key"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.task_results: list[dict[str, Any]] = []
        self.submit_result: dict[str, Any] = {
            "local_task_id": "task-1",
            "status": "running",
        }
        self.auth_error: CoreApiError | None = None

    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: Any = None,
        **_kwargs: Any,
    ) -> Any:
        self.calls.append(
            {"method": method, "path": path, "params": params, "payload": payload}
        )
        if path == "/api/v1/tasks" and method == "GET":
            if self.auth_error is not None:
                raise self.auth_error
            return {"tasks": [], "total": 3}
        if path == "/api/v1/tested-models":
            return {
                "models": [
                    {"id": "m1", "api_key": "sk-12345678"},
                    {"id": "m2", "api_key": "***"},
                ],
                "platform_models": [
                    {"id": "p1", "api_key": "sk-plat-12345678"},
                ],
            }
        if path == "/api/v1/skills/file-manage/files":
            return {"files": [], "total": 0}
        if path.startswith("/api/v1/tasks/") and path.endswith("/files"):
            return {"files": [{"file_id": "f1", "role": "report"}]}
        if path.startswith("/api/v1/tasks/") and path.endswith("/cancel"):
            return {
                "task_id": "task-1",
                "cancelled": True,
                "status": "failed",
                "reason": "cancelled",
            }
        if path.startswith("/api/v1/tasks/"):
            if not self.task_results:
                raise AssertionError(f"no task result queued for {path}")
            return self.task_results.pop(0)
        if path.endswith("/credentials"):
            return {"env_name": "K", "model_name": "m", "source": "own"}
        if path.endswith(("/detect", "/batch", "/eval")):
            return dict(self.submit_result)
        raise AssertionError(f"unexpected path: {path}")

    async def upload_file(
        self, path: Path | str, file_type: str, *, timeout: float | None = None
    ) -> Any:
        self.calls.append({"path": "upload", "file": str(path), "type": file_type})
        return {
            "file_id": "file-1",
            "storage_uri": f"/data/user_storage/{file_type}/u/x.jsonl",
            "size_bytes": 3,
        }

    async def download_file(
        self,
        file_id: str,
        *,
        dest: Path | str | None = None,
        dest_dir: Path | str | None = None,
        overwrite: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"path": "download", "file_id": file_id, "dest": dest, "dest_dir": dest_dir}
        )
        return {"file_id": file_id, "path": "/tmp/x", "size_bytes": 3}

    async def read_file_content(self, file_id: str) -> Any:
        return {"content": f"content-{file_id}"}

    async def close(self) -> None:
        pass


async def test_status_success():
    ops = CoreOps(FakeClient())
    data = await ops.status()
    assert data["health_ok"] is True
    assert data["auth_ok"] is True
    assert data["task_count"] == 3
    assert data["base_url"] == "http://core:8080"


async def test_status_reports_auth_error():
    client = FakeClient()
    client.auth_error = CoreApiError(CoreApiErrorKind.AUTH, "bad key", status=401)
    data = await CoreOps(client).status()
    assert data["health_ok"] is True
    assert data["auth_ok"] is False
    assert "bad key" in data["auth_error"]


async def test_status_without_api_key():
    client = FakeClient()
    client.api_key = ""
    data = await CoreOps(client).status()
    assert data["api_key_set"] is False
    assert data["auth_ok"] is False


async def test_list_models_masks_own_keys():
    data = await CoreOps(FakeClient()).list_models()
    assert data["models"][0]["api_key"] == "sk-***"
    assert data["models"][1]["api_key"] == "***"
    assert data["platform_models"][0]["api_key"] == "sk-***"


async def test_inject_credentials_payload():
    client = FakeClient()
    data = await CoreOps(client).inject_credentials(
        "safety-eval", env_name="DEEPSEEK_API_KEY", model_id="m1", overwrite=False
    )
    assert data["source"] == "own"
    call = client.calls[-1]
    assert call["path"] == "/api/v1/skills/safety-eval/credentials"
    assert call["payload"] == {
        "env_name": "DEEPSEEK_API_KEY",
        "model_id": "m1",
        "overwrite": False,
    }


async def test_upload_dataset_builds_config(tmp_path: Path):
    src = tmp_path / "data.jsonl"
    src.write_text("{}\n")
    client = FakeClient()
    upload, dataset = await CoreOps(client).upload_dataset(
        src, data_format="case_list"
    )
    assert upload["file_id"] == "file-1"
    assert dataset == {
        "source_type": "server_path",
        "path": "/data/user_storage/dataset/u/x.jsonl",
        "file_format": "jsonl",
        "data_format": "case_list",
    }


async def test_build_dataset_rejects_missing_storage_uri():
    with pytest.raises(CoreApiError) as exc:
        CoreOps.build_dataset({"file_id": "x"})
    assert exc.value.kind is CoreApiErrorKind.BUSINESS


async def test_task_wait_succeeded_immediately():
    client = FakeClient()
    client.task_results = [{"status": "succeeded", "output_payload": {"ok": True}}]
    seen: list[str] = []
    task = await CoreOps(client).task_wait(
        "task-1", interval=0.01, timeout=1, on_status=lambda t: seen.append(t["status"])
    )
    assert task["status"] == "succeeded"
    assert seen == ["succeeded"]


async def test_task_wait_running_then_succeeded():
    client = FakeClient()
    client.task_results = [
        {"status": "running"},
        {"status": "succeeded", "output_payload": {}},
    ]
    task = await CoreOps(client).task_wait("task-1", interval=0.01, timeout=1)
    assert task["status"] == "succeeded"


async def test_task_wait_failed_raises():
    client = FakeClient()
    client.task_results = [
        {"status": "failed", "error_message": "Cancelled by user"}
    ]
    with pytest.raises(CoreApiError) as exc:
        await CoreOps(client).task_wait("task-1", interval=0.01, timeout=1)
    assert exc.value.kind is CoreApiErrorKind.TASK_FAILED
    assert "Cancelled by user" in str(exc.value)


async def test_task_wait_timeout():
    client = FakeClient()
    client.task_results = [{"status": "running"}, {"status": "running"}]
    with pytest.raises(CoreApiError) as exc:
        await CoreOps(client).task_wait("task-1", interval=0.01, timeout=0.01)
    assert exc.value.kind is CoreApiErrorKind.TIMEOUT


async def test_task_wait_missing_status_errors():
    client = FakeClient()
    client.task_results = [{}]
    with pytest.raises(CoreApiError) as exc:
        await CoreOps(client).task_wait("task-1", interval=0.01, timeout=1)
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_task_cancel_non_dict_errors():
    class BadCancel(FakeClient):
        async def request_json(self, method, path, **kwargs):
            return None

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(BadCancel()).task_cancel("task-1")
    assert exc.value.kind is CoreApiErrorKind.BUSINESS


async def test_task_get_non_dict_is_not_found():
    class EmptyClient(FakeClient):
        async def request_json(self, method, path, **kwargs):
            return None

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(EmptyClient()).task_get("task-1")
    assert exc.value.kind is CoreApiErrorKind.NOT_FOUND


async def test_submit_detection_requires_task_id():
    client = FakeClient()
    client.submit_result = {"status": "running"}
    with pytest.raises(CoreApiError) as exc:
        await CoreOps(client).submit_detection("text-detect", {"text": "x"})
    assert exc.value.kind is CoreApiErrorKind.BUSINESS


async def test_task_artifacts_params():
    client = FakeClient()
    data = await CoreOps(client).task_artifacts("task-1", role="report", skip=1, limit=5)
    assert data["files"][0]["file_id"] == "f1"
    call = client.calls[-1]
    assert call["params"] == {"skip": 1, "limit": 5, "role": "report"}


async def test_task_cancel():
    client = FakeClient()
    data = await CoreOps(client).task_cancel("task-1")
    assert data["cancelled"] is True
    assert client.calls[-1]["method"] == "POST"


async def test_download_delegates_dest_dir(tmp_path: Path):
    client = FakeClient()
    data = await CoreOps(client, download_dir=tmp_path).download_file("f1")
    assert data["file_id"] == "f1"
    call = client.calls[-1]
    assert call["dest_dir"] == tmp_path


async def test_status_accepts_disabled_jobs_reaper():
    class DisabledReaper(FakeClient):
        async def health(self):
            return {
                "status": "ok",
                "jobs_reaper": {"enabled": False, "alive": False},
            }

    data = await CoreOps(DisabledReaper()).status()
    assert data["health_ok"] is True


async def test_status_rejects_dead_enabled_reaper():
    class DeadReaper(FakeClient):
        async def health(self):
            return {
                "status": "ok",
                "jobs_reaper": {"enabled": True, "alive": False},
            }

    data = await CoreOps(DeadReaper()).status()
    assert data["health_ok"] is False


async def test_task_get_detail_object_rejected():
    class DetailTask(FakeClient):
        async def request_json(self, method, path, **kwargs):
            return {"detail": "Unauthorized"}

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(DetailTask()).task_get("task-1")
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_task_cancel_detail_object_rejected():
    class DetailCancel(FakeClient):
        async def request_json(self, method, path, **kwargs):
            return {"detail": "Unauthorized"}

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(DetailCancel()).task_cancel("task-1")
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_status_rejects_non_dict_health():
    class BadHealth(FakeClient):
        async def health(self):
            return "<html>gateway login</html>"

    data = await CoreOps(BadHealth()).status()
    assert data["health_ok"] is False


async def test_list_models_rejects_non_dict_response():
    class BadModels(FakeClient):
        async def request_json(self, method, path, **kwargs):
            if path == "/api/v1/tested-models":
                return "<html>login</html>"
            return await super().request_json(method, path, **kwargs)

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(BadModels()).list_models()
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_platform_model_key_is_masked():
    data = await CoreOps(FakeClient()).list_models()
    assert data["platform_models"][0]["api_key"] == "sk-***"


def test_build_dataset_rejects_csv_and_bad_formats():
    with pytest.raises(ValueError):
        CoreOps.build_dataset(
            {"storage_uri": "/data/dataset/x.csv"}, file_format=None
        )
    with pytest.raises(ValueError):
        CoreOps.build_dataset(
            {"storage_uri": "/data/dataset/x.jsonl"}, file_format="csv"
        )
    with pytest.raises(ValueError):
        CoreOps.build_dataset(
            {"storage_uri": "/data/dataset/x.jsonl"}, data_format="bad"
        )


async def test_upload_dataset_rejects_csv_before_upload(tmp_path: Path):
    src = tmp_path / "data.csv"
    src.write_text("a,b\n", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(ValueError):
        await CoreOps(client).upload_dataset(src)
    assert not any(c.get("path") == "upload" for c in client.calls)


async def test_task_wait_pending_then_succeeded():
    client = FakeClient()
    client.task_results = [
        {"status": "pending"},
        {"status": "succeeded", "output_payload": {}},
    ]
    task = await CoreOps(client).task_wait("task-1", interval=0.01, timeout=1)
    assert task["status"] == "succeeded"


async def test_list_models_rejects_detail_object():
    class DetailModels(FakeClient):
        async def request_json(self, method, path, **kwargs):
            if path == "/api/v1/tested-models":
                return {"detail": "Unauthorized"}
            return await super().request_json(method, path, **kwargs)

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(DetailModels()).list_models()
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_task_list_rejects_detail_object():
    class DetailTasks(FakeClient):
        async def request_json(self, method, path, **kwargs):
            if path == "/api/v1/tasks" and method == "GET":
                return {"detail": "Unauthorized"}
            return await super().request_json(method, path, **kwargs)

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(DetailTasks()).task_list()
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_upload_response_requires_file_id():
    class BadUpload(FakeClient):
        async def upload_file(self, path, file_type, **kwargs):
            return {"storage_uri": "/data/x"}

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(BadUpload()).upload_file("x.txt", "dataset")
    assert exc.value.kind is CoreApiErrorKind.SERVER


async def test_task_list_rejects_non_dict_response():
    class BadTasks(FakeClient):
        async def request_json(self, method, path, **kwargs):
            if path == "/api/v1/tasks" and method == "GET":
                return "<html>login</html>"
            return await super().request_json(method, path, **kwargs)

    with pytest.raises(CoreApiError) as exc:
        await CoreOps(BadTasks()).task_list()
    assert exc.value.kind is CoreApiErrorKind.SERVER
