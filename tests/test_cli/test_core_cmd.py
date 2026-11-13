"""core 命令测试：CliRunner + fake CoreOps，不跑真实网络。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from saitec_cli.cli.commands.core import _shared
from saitec_cli.cli.main import app

runner = CliRunner()


def _config() -> dict:
    return {
        "detector": {
            "url": "http://core:8080",
            "api_key": "sk-test-12345678",
            "endpoint_path": "/api/v1/cli/safe-guard/detect",
            "report_interval_sec": 60,
            "batch_size": 500,
            "max_queue_size": 10000,
            "timeout_sec": 30,
        },
        "services": [],
        "log_level": "INFO",
    }


class FakeClient:
    def __init__(self, base_url, api_key, *, timeout_sec=30, **_kwargs):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeOps:
    last: "FakeOps | None" = None
    status_data: dict = {}

    def __init__(self, client, *, download_dir=None):
        self.client = client
        self.download_dir = download_dir
        self.calls: list[tuple[str, dict]] = []
        FakeOps.last = self

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    async def status(self):
        self._record("status")
        return dict(FakeOps.status_data)

    async def list_models(self, **kwargs):
        self._record("list_models", **kwargs)
        return {
            "models": [{"id": "m1", "api_key": "sk-***"}],
            "platform_models": [{"id": "p1", "unit_price_amount": 10}],
            "total": 1,
        }

    async def inject_credentials(self, skill, **kwargs):
        self._record("inject_credentials", skill=skill, **kwargs)
        return {"env_name": "K", "model_name": "m", "source": "own"}

    async def upload_file(self, path, file_type, *, timeout=None):
        self._record(
            "upload_file", path=str(path), file_type=file_type, timeout=timeout
        )
        return {
            "file_id": "file-1",
            "storage_uri": f"/data/{file_type}/x",
            "size_bytes": 3,
            "sha256": "abc",
            "file_type": file_type,
        }

    async def download_file(self, file_id, **kwargs):
        self._record("download_file", file_id=file_id, **kwargs)
        return {"file_id": file_id, "path": "/tmp/out", "size_bytes": 3}

    async def list_files(self, **kwargs):
        self._record("list_files", **kwargs)
        return {"files": [{"file_id": "f1", "role": "dataset"}], "total": 1}

    async def read_file(self, file_id):
        self._record("read_file", file_id=file_id)
        return {"content": "hello"}

    async def upload_dataset(self, path, **kwargs):
        self._record("upload_dataset", path=str(path), **kwargs)
        return (
            {"file_id": "f1", "storage_uri": "/data/dataset/x.jsonl"},
            {
                "source_type": "server_path",
                "path": "/data/dataset/x.jsonl",
                "file_format": "jsonl",
                "data_format": "case_list",
            },
        )

    async def submit_detection(self, skill, payload):
        self._record("submit_detection", skill=skill, payload=payload)
        return {"local_task_id": "task-1", "status": "running"}

    async def submit_batch_detection(self, skill, payload):
        self._record("submit_batch_detection", skill=skill, payload=payload)
        return {"local_task_id": "task-1", "status": "running"}

    async def submit_eval(self, skill, payload):
        self._record("submit_eval", skill=skill, payload=payload)
        return {"local_task_id": "task-1", "status": "running"}

    async def task_list(self, **kwargs):
        self._record("task_list", **kwargs)
        return {"tasks": [{"local_task_id": "task-1"}], "total": 1}

    async def task_get(self, task_id):
        self._record("task_get", task_id=task_id)
        return {"local_task_id": task_id, "status": "succeeded"}

    async def task_wait(self, task_id, **kwargs):
        on_status = kwargs.pop("on_status", None)
        self._record("task_wait", task_id=task_id, **kwargs)
        if on_status is not None:
            on_status({"status": "succeeded"})
        return {
            "local_task_id": task_id,
            "status": "succeeded",
            "task_type": "text_detect",
            "output_payload": {"label": "human"},
        }

    async def task_cancel(self, task_id):
        self._record("task_cancel", task_id=task_id)
        return {
            "task_id": task_id,
            "cancelled": True,
            "status": "failed",
            "reason": "cancelled",
        }

    async def task_artifacts(self, task_id, **kwargs):
        self._record("task_artifacts", task_id=task_id, **kwargs)
        return {"files": [{"file_id": "f1", "role": "report"}], "total": 1}


DEFAULT_STATUS = {
    "base_url": "http://core:8080",
    "api_key_set": True,
    "health_ok": True,
    "auth_ok": True,
    "task_count": 2,
}


@pytest.fixture
def core_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    monkeypatch.setenv("SAITEC_CLI_CONFIG", str(config_path))
    monkeypatch.setattr(_shared, "CoreClient", FakeClient)
    monkeypatch.setattr(_shared, "CoreOps", FakeOps)
    FakeOps.last = None
    FakeOps.status_data = dict(DEFAULT_STATUS)
    return config_path


def _json(result) -> dict:
    return json.loads(result.stdout)


def test_core_status_ok(core_env):
    result = runner.invoke(app, ["core", "status", "--json"])
    assert result.exit_code == 0
    data = _json(result)
    assert data["ok"] is True
    assert data["data"]["auth_ok"] is True


def test_core_status_failure_exit_3(core_env):
    FakeOps.status_data = {
        **DEFAULT_STATUS,
        "auth_ok": False,
        "auth_error": "Invalid or expired API Key",
    }
    result = runner.invoke(app, ["core", "status", "--json"])
    assert result.exit_code == 3
    data = _json(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "STATUS_FAILED"


def test_core_models_list(core_env):
    result = runner.invoke(app, ["core", "models", "list", "--limit", "5", "--json"])
    assert result.exit_code == 0
    assert FakeOps.last.calls[-1] == ("list_models", {"skip": 0, "limit": 5})
    data = _json(result)
    assert data["data"]["models"][0]["api_key"] == "sk-***"


def test_core_credentials_set(core_env):
    result = runner.invoke(
        app,
        [
            "core", "credentials", "set", "--skill", "safety-eval",
            "--env-name", "DEEPSEEK_API_KEY", "--model-id", "m1",
            "--no-overwrite", "--json",
        ],
    )
    assert result.exit_code == 0
    assert FakeOps.last.calls[-1] == (
        "inject_credentials",
        {
            "skill": "safety-eval",
            "env_name": "DEEPSEEK_API_KEY",
            "model_id": "m1",
            "overwrite": False,
        },
    )


def test_core_file_upload_infers_type(core_env, tmp_path: Path):
    image = tmp_path / "x.png"
    image.write_bytes(b"png")
    result = runner.invoke(app, ["core", "file", "upload", str(image), "--json"])
    assert result.exit_code == 0
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "upload_file"
    assert kwargs["file_type"] == "image"
    assert kwargs["timeout"] == 300.0


def test_core_file_upload_unknown_extension_is_user_error(core_env, tmp_path: Path):
    weird = tmp_path / "x.unknown"
    weird.write_bytes(b"x")
    result = runner.invoke(app, ["core", "file", "upload", str(weird), "--json"])
    assert result.exit_code == 1
    data = _json(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "INVALID_ARGUMENT"


def test_core_file_download(core_env, tmp_path: Path):
    out = tmp_path / "out.bin"
    result = runner.invoke(
        app,
        ["core", "file", "download", "f1", "-o", str(out), "--overwrite", "--json"],
    )
    assert result.exit_code == 0
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "download_file"
    assert kwargs["dest"] == str(out)
    assert kwargs["overwrite"] is True
    assert kwargs["timeout"] == 300.0


def test_core_file_download_keeps_dir_trailing_slash(core_env):
    result = runner.invoke(
        app,
        ["core", "file", "download", "f1", "-o", "./downloads/", "--json"],
    )
    assert result.exit_code == 0
    _, kwargs = FakeOps.last.calls[-1]
    assert kwargs["dest"] == "./downloads/"


def test_core_file_list_and_read(core_env):
    listed = runner.invoke(app, ["core", "file", "list", "--json"])
    assert listed.exit_code == 0
    read = runner.invoke(app, ["core", "file", "read", "f1", "--json"])
    assert read.exit_code == 0
    assert _json(read)["data"] == {"content": "hello"}


def test_core_task_list(core_env):
    result = runner.invoke(
        app,
        ["core", "task", "list", "--task-type", "text_detect", "--limit", "5", "--json"],
    )
    assert result.exit_code == 0
    assert FakeOps.last.calls[-1] == (
        "task_list",
        {
            "task_type": "text_detect",
            "skip": 0,
            "limit": 5,
            "created_start": None,
            "created_end": None,
        },
    )


def test_core_task_get(core_env):
    result = runner.invoke(app, ["core", "task", "get", "task-1", "--json"])
    assert result.exit_code == 0
    assert _json(result)["data"]["status"] == "succeeded"


def test_global_json_flag_before_core(core_env):
    result = runner.invoke(app, ["--json", "core", "status"])
    assert result.exit_code == 0
    assert _json(result)["data"]["auth_ok"] is True


def test_global_json_wait_progress_is_json(core_env):
    result = runner.invoke(
        app,
        ["--json", "core", "detect", "text", "--text", "x", "--wait"],
    )
    assert result.exit_code == 0
    progress = [line for line in result.stderr.splitlines() if line.strip()]
    assert progress
    for line in progress:
        json.loads(line)


def test_global_json_before_core_skill(tmp_path: Path):
    doc = tmp_path / "a.md"
    doc.write_text("# A\n", encoding="utf-8")
    result = runner.invoke(
        app, ["--json", "core", "skill", "list", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert _json(result)["ok"] is True


def test_core_task_get_markup_content_does_not_crash(core_env):
    class MarkupOps(FakeOps):
        async def task_get(self, task_id):
            return {
                "local_task_id": task_id,
                "status": "succeeded",
                "output_payload": {"note": "[/]"},
            }

    _shared.CoreOps = MarkupOps
    result = runner.invoke(app, ["core", "task", "get", "task-1"])
    assert result.exit_code == 0
    assert "[/]" in result.stdout


def test_core_file_read_markup_content_preserved(core_env):
    class MarkupOps(FakeOps):
        async def read_file(self, file_id):
            return {"content": "see [docs](http://example.com) and [/]"}

    _shared.CoreOps = MarkupOps
    result = runner.invoke(app, ["core", "file", "read", "f1"])
    assert result.exit_code == 0
    assert "see [docs](http://example.com) and [/]" in result.stdout


def test_core_status_error_markup_does_not_crash(core_env):
    FakeOps.status_data = {
        **DEFAULT_STATUS,
        "auth_ok": False,
        "auth_error": "[/]",
    }
    result = runner.invoke(app, ["core", "status"])
    assert result.exit_code == 3
    assert "[/]" in result.stderr or "[/]" in result.stdout


def test_core_task_wait(core_env):
    result = runner.invoke(
        app,
        ["core", "task", "wait", "task-1", "--interval", "0.1", "--timeout", "2", "--json"],
    )
    assert result.exit_code == 0
    assert _json(result)["data"]["status"] == "succeeded"
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "task_wait"
    assert kwargs["interval"] == 0.1
    assert kwargs["timeout"] == 2.0


def test_core_task_cancel_not_found(core_env):
    class NotFoundOps(FakeOps):
        async def task_cancel(self, task_id):
            return {"task_id": task_id, "cancelled": False, "reason": "not_found"}

    _shared.CoreOps = NotFoundOps
    result = runner.invoke(app, ["core", "task", "cancel", "task-1", "--json"])
    assert result.exit_code == 1
    assert _json(result)["error"]["code"] == "NOT_FOUND"


def test_core_task_artifacts(core_env):
    result = runner.invoke(
        app,
        ["core", "task", "artifacts", "task-1", "--role", "report", "--json"],
    )
    assert result.exit_code == 0
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "task_artifacts"
    assert kwargs["role"] == "report"


def test_core_detect_text(core_env):
    result = runner.invoke(
        app,
        ["core", "detect", "text", "--text", "hello", "--method", "sample", "--json"],
    )
    assert result.exit_code == 0
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "submit_detection"
    assert kwargs["skill"] == "text-detect"
    assert kwargs["payload"] == {"text": "hello", "method": "sample"}


def test_core_detect_text_wait(core_env):
    result = runner.invoke(
        app,
        ["core", "detect", "text", "--text", "hello", "--wait",
         "--interval", "0.1", "--timeout", "2", "--json"],
    )
    assert result.exit_code == 0
    assert _json(result)["data"]["status"] == "succeeded"


def test_core_detect_image_file_auto_upload(core_env, tmp_path: Path):
    image = tmp_path / "x.png"
    image.write_bytes(b"png")
    result = runner.invoke(app, ["core", "detect", "image", "--file", str(image), "--json"])
    assert result.exit_code == 0
    calls = FakeOps.last.calls
    assert calls[0][0] == "upload_file"
    assert calls[0][1]["file_type"] == "image"
    name, kwargs = calls[-1]
    assert name == "submit_detection"
    assert kwargs["payload"]["image_uri"] == "/data/image/x"


def test_core_detect_text_batch_items(core_env, tmp_path: Path):
    items = tmp_path / "items.json"
    items.write_text(json.dumps([{"id": "1", "text": "x"}]), encoding="utf-8")
    result = runner.invoke(
        app,
        ["core", "detect", "text-batch", "--items", str(items), "--json"],
    )
    assert result.exit_code == 0
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "submit_batch_detection"
    assert kwargs["payload"] == {"items": [{"id": "1", "text": "x"}]}


def test_core_detect_text_batch_dataset_file(core_env, tmp_path: Path):
    dataset = tmp_path / "data.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["core", "detect", "text-batch", "--dataset-file", str(dataset), "--json"],
    )
    assert result.exit_code == 0
    names = [c[0] for c in FakeOps.last.calls]
    assert "upload_dataset" in names
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "submit_batch_detection"
    assert kwargs["payload"]["dataset"]["path"] == "/data/dataset/x.jsonl"


def test_core_detect_image_batch_files(core_env, tmp_path: Path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    result = runner.invoke(
        app,
        [
            "core", "detect", "image-batch",
            "--files", str(a), "--files", str(b), "--json",
        ],
    )
    assert result.exit_code == 0
    names = [c[0] for c in FakeOps.last.calls]
    assert names == ["upload_file", "upload_file", "submit_batch_detection"]
    payload = FakeOps.last.calls[-1][1]["payload"]
    assert len(payload["items"]) == 2
    assert all(item["image_uri"].startswith("/data/image/") for item in payload["items"])


def test_core_eval_safety_payload(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "safety", "--task-name", "t",
            "--prompt", "p", "--caller-model", "m",
            "--caller-api-key-env", "K", "--judge-model-name", "j",
            "--attack-name", "a", "--risk-category", "r",
            "--safety-rules-text", "rules", "--json",
        ],
    )
    assert result.exit_code == 0
    name, kwargs = FakeOps.last.calls[-1]
    assert name == "submit_eval"
    assert kwargs["skill"] == "safety-eval"
    assert kwargs["payload"] == {
        "task_name": "t",
        "prompts": ["p"],
        "caller": {
            "adapter_type": "openai",
            "model": "m",
            "api_key_env": "K",
        },
        "judge_model_name": "j",
        "attacks": {"attack_names": ["a"]},
        "safety_rules_text": "rules",
        "risk_categories": ["r"],
    }


def test_core_eval_general_payload_with_provider_false(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "general", "--task-name", "t",
            "--prompt", "p", "--judge-caller-json",
            '{"adapter_type": "openai", "model": "j"}',
            "--dimension", "d1", "--no-provider-block-response-text", "--json",
        ],
    )
    assert result.exit_code == 0
    _, kwargs = FakeOps.last.calls[-1]
    payload = kwargs["payload"]
    assert payload["provider_block_response_text"] is False
    assert payload["judge_callers"] == [{"adapter_type": "openai", "model": "j"}]
    assert payload["dimensions"] == ["d1"]


def test_core_eval_general_provider_flags_conflict(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "general", "--task-name", "t",
            "--provider-block-response-text",
            "--no-provider-block-response-text", "--json",
        ],
    )
    assert result.exit_code == 1
    assert _json(result)["error"]["code"] == "INVALID_ARGUMENT"
    assert not any(c[0] == "submit_eval" for c in FakeOps.last.calls)


def test_core_eval_corpus_payload(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "corpus", "--task-name", "t", "--text", "x",
            "--judge-model-name", "j", "--no-chunking-enabled", "--json",
        ],
    )
    assert result.exit_code == 0
    _, kwargs = FakeOps.last.calls[-1]
    assert kwargs["skill"] == "corpus-safety-eval"
    assert kwargs["payload"] == {
        "task_name": "t",
        "judge_model_name": "j",
        "texts": ["x"],
        "chunking": {"enabled": False},
    }


def test_core_eval_prompt_and_dataset_conflict(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "safety", "--task-name", "t",
            "--prompt", "p", "--dataset-path", "/data/d.jsonl", "--json",
        ],
    )
    assert result.exit_code == 1
    assert _json(result)["error"]["code"] == "INVALID_ARGUMENT"


def test_core_eval_dataset_source_type_with_file_rejected(core_env, tmp_path: Path):
    dataset = tmp_path / "d.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "core", "eval", "safety", "--task-name", "t",
            "--dataset-file", str(dataset), "--dataset-source-type", "weird", "--json",
        ],
    )
    assert result.exit_code == 1
    assert _json(result)["error"]["code"] == "INVALID_ARGUMENT"
    assert not any(c[0] == "upload_dataset" for c in FakeOps.last.calls)


def test_core_eval_requires_input(core_env):
    for args in (
        ["core", "eval", "safety", "--task-name", "t"],
        ["core", "eval", "general", "--task-name", "t"],
        ["core", "eval", "corpus", "--task-name", "t"],
    ):
        result = runner.invoke(app, [*args, "--json"])
        assert result.exit_code == 1
        assert _json(result)["error"]["code"] == "INVALID_ARGUMENT"
        assert not any(c[0] == "submit_eval" for c in FakeOps.last.calls)


def test_core_eval_safety_caller_siliconflow_rejected(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "safety", "--task-name", "t", "--prompt", "p",
            "--caller-adapter-type", "siliconflow_image_generation",
            "--caller-model", "m", "--caller-api-key-env", "K", "--json",
        ],
    )
    assert result.exit_code == 1
    assert "caller 不支持" in _json(result)["error"]["message"]


def test_core_eval_judge_siliconflow_rejected(core_env):
    result = runner.invoke(
        app,
        [
            "core", "eval", "safety", "--task-name", "t", "--prompt", "p",
            "--judge-caller-adapter-type", "siliconflow_image_generation", "--json",
        ],
    )
    assert result.exit_code == 1
    assert "judge_caller 不支持" in _json(result)["error"]["message"]


def test_core_skill_list_get_search(tmp_path: Path):
    doc = tmp_path / "alpha.md"
    doc.write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\nhello world\n",
        encoding="utf-8",
    )
    (tmp_path / "beta.md").write_text("# Beta\nother\n", encoding="utf-8")

    listed = runner.invoke(app, ["core", "skill", "list", "--dir", str(tmp_path), "--json"])
    assert listed.exit_code == 0
    names = [s["name"] for s in _json(listed)["data"]["skills"]]
    assert "alpha" in names

    got = runner.invoke(
        app, ["core", "skill", "get", "alpha", "--dir", str(tmp_path), "--json"]
    )
    assert got.exit_code == 0
    assert "hello world" in _json(got)["data"]["content"]

    searched = runner.invoke(
        app, ["core", "skill", "search", "hello", "--dir", str(tmp_path), "--json"]
    )
    assert searched.exit_code == 0
    assert _json(searched)["data"]["matches"][0]["file"] == "alpha.md"
