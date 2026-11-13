"""core 检测全链路测试：真实 CLI → HTTP → 本地 mock Core。

与 test_core_cmd.py 的区别：不替换 CoreOps，走真实 CoreClient/aiohttp，
验证配置加载、请求体、BaseResponse 解包、--json 输出与退出码的完整链路。
覆盖文本/图像/视频的单条与批量检测，以及提交后立即取消的终态语义。
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from typer.testing import CliRunner

from saitec_cli.cli.main import app

runner = CliRunner()

_SKILLS = ("text-detect", "image-detect", "video-detect")


class _FakeCore:
    """在独立线程/事件循环里运行的最小 Core mock。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.base_url = ""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._runner: web.AppRunner | None = None

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())
        self._ready.set()
        self._loop.run_forever()

    async def _start(self) -> None:
        web_app = web.Application()
        for skill in _SKILLS:
            web_app.router.add_post(
                f"/api/v1/skills/{skill}/detect", self._detect
            )
            web_app.router.add_post(f"/api/v1/skills/{skill}/batch", self._batch)
        web_app.router.add_post(
            "/api/v1/skills/file-manage/upload", self._upload
        )
        web_app.router.add_post(
            "/api/v1/tasks/{task_id}/cancel", self._cancel
        )
        self._runner = web.AppRunner(web_app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    def start(self) -> "_FakeCore":
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("mock Core 启动超时")
        return self

    def stop(self) -> None:
        if self._runner is not None:
            asyncio.run_coroutine_threadsafe(
                self._runner.cleanup(), self._loop
            ).result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()

    def _task_data(self) -> dict[str, Any]:
        submitted = len(
            [r for r in self.requests if r["kind"] in ("detect", "batch")]
        )
        return {"local_task_id": f"task-{submitted}", "status": "running"}

    async def _detect(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(
            {
                "kind": "detect",
                "path": request.path,
                "body": body,
                "api_key": request.headers.get("X-API-Key"),
            }
        )
        return web.json_response(
            {"success": True, "message": "success", "data": self._task_data()}
        )

    async def _batch(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(
            {"kind": "batch", "path": request.path, "body": body}
        )
        return web.json_response(
            {"success": True, "message": "success", "data": self._task_data()}
        )

    async def _upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        fields: dict[str, str] = {}
        filename = ""
        size = 0
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                filename = part.filename or ""
                size = len(await part.read())
            else:
                fields[part.name] = await part.text()
        file_type = fields.get("file_type", "")
        self.requests.append(
            {
                "kind": "upload",
                "path": request.path,
                "filename": filename,
                "file_type": file_type,
                "size": size,
            }
        )
        upload_index = len([r for r in self.requests if r["kind"] == "upload"])
        return web.json_response(
            {
                "success": True,
                "message": "success",
                "data": {
                    "file_id": f"file-{upload_index}",
                    "storage_uri": f"/data/{file_type}/{filename}",
                    "size_bytes": size,
                },
            }
        )

    async def _cancel(self, request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        self.requests.append(
            {"kind": "cancel", "path": request.path, "task_id": task_id}
        )
        return web.json_response(
            {
                "success": True,
                "message": "success",
                "data": {
                    "task_id": task_id,
                    "cancelled": True,
                    "status": "failed",
                    "reason": "cancelled",
                },
            }
        )


@pytest.fixture
def fake_core() -> _FakeCore:
    server = _FakeCore().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def core_env(tmp_path: Path, fake_core: _FakeCore) -> dict[str, str]:
    config = {
        "detector": {
            "url": fake_core.base_url,
            "api_key": "test-key",
            "endpoint_path": "/api/v1/cli/safe-guard/detect",
            "report_interval_sec": 60,
            "batch_size": 500,
            "max_queue_size": 10000,
            "timeout_sec": 30,
        },
        "services": [],
        "log_level": "INFO",
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return {"SAITEC_CLI_CONFIG": str(config_path)}


def _invoke(env: dict[str, str], *args: str) -> dict[str, Any]:
    result = runner.invoke(app, [*args, "--json"], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    return payload["data"]


def _submit_and_cancel(
    env: dict[str, str], server: _FakeCore, *create_args: str
) -> dict[str, Any]:
    """创建检测任务 → 立即取消；返回提交响应。"""
    created = _invoke(env, *create_args)
    task_id = created["local_task_id"]
    cancelled = _invoke(env, "core", "task", "cancel", task_id)
    assert cancelled["task_id"] == task_id
    assert cancelled["cancelled"] is True
    return created


def _requests(server: _FakeCore, kind: str) -> list[dict[str, Any]]:
    return [item for item in server.requests if item["kind"] == kind]


def test_text_detect_create_then_cancel(
    core_env: dict[str, str], fake_core: _FakeCore
) -> None:
    _submit_and_cancel(
        core_env,
        fake_core,
        "core",
        "detect",
        "text",
        "--text",
        "hello world",
        "--method",
        "sample",
    )

    detects = _requests(fake_core, "detect")
    assert detects[0]["path"] == "/api/v1/skills/text-detect/detect"
    assert detects[0]["body"] == {"text": "hello world", "method": "sample"}
    assert detects[0]["api_key"] == "test-key"
    assert _requests(fake_core, "cancel")[0]["task_id"] == "task-1"


def test_text_batch_create_then_cancel(
    core_env: dict[str, str], fake_core: _FakeCore, tmp_path: Path
) -> None:
    items = tmp_path / "items.json"
    items.write_text(
        json.dumps([{"id": "case-1", "text": "a"}, {"id": "case-2", "text": "b"}]),
        encoding="utf-8",
    )

    _submit_and_cancel(
        core_env,
        fake_core,
        "core",
        "detect",
        "text-batch",
        "--items",
        str(items),
    )

    batches = _requests(fake_core, "batch")
    assert batches[0]["path"] == "/api/v1/skills/text-detect/batch"
    assert [item["id"] for item in batches[0]["body"]["items"]] == [
        "case-1",
        "case-2",
    ]


def test_image_detect_uploads_then_detects(
    core_env: dict[str, str], fake_core: _FakeCore, tmp_path: Path
) -> None:
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    _submit_and_cancel(
        core_env,
        fake_core,
        "core",
        "detect",
        "image",
        "--file",
        str(image),
        "--method",
        "deepfake_defenders",
    )

    uploads = _requests(fake_core, "upload")
    detects = _requests(fake_core, "detect")
    assert uploads[0]["file_type"] == "image"
    assert uploads[0]["filename"] == "photo.png"
    assert detects[0]["path"] == "/api/v1/skills/image-detect/detect"
    assert detects[0]["body"]["image_uri"] == "/data/image/photo.png"
    assert detects[0]["body"]["method"] == "deepfake_defenders"


def test_image_batch_uploads_each_then_detects(
    core_env: dict[str, str], fake_core: _FakeCore, tmp_path: Path
) -> None:
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    _submit_and_cancel(
        core_env,
        fake_core,
        "core",
        "detect",
        "image-batch",
        "--files",
        str(first),
        "--files",
        str(second),
        "--method",
        "tamper_yolo",
    )

    uploads = _requests(fake_core, "upload")
    batches = _requests(fake_core, "batch")
    assert [u["file_type"] for u in uploads] == ["image", "image"]
    assert batches[0]["path"] == "/api/v1/skills/image-detect/batch"
    assert batches[0]["body"]["method"] == "tamper_yolo"
    assert batches[0]["body"]["items"] == [
        {"id": "a", "image_uri": "/data/image/a.png"},
        {"id": "b", "image_uri": "/data/image/b.png"},
    ]


def test_video_detect_uploads_then_detects(
    core_env: dict[str, str], fake_core: _FakeCore, tmp_path: Path
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4")

    _submit_and_cancel(
        core_env,
        fake_core,
        "core",
        "detect",
        "video",
        "--file",
        str(video),
    )

    uploads = _requests(fake_core, "upload")
    detects = _requests(fake_core, "detect")
    assert uploads[0]["file_type"] == "video"
    assert uploads[0]["filename"] == "clip.mp4"
    assert detects[0]["path"] == "/api/v1/skills/video-detect/detect"
    assert detects[0]["body"]["video_uri"] == "/data/video/clip.mp4"


def test_video_batch_uploads_each_then_detects(
    core_env: dict[str, str], fake_core: _FakeCore, tmp_path: Path
) -> None:
    first = tmp_path / "one.mp4"
    second = tmp_path / "two.mp4"
    first.write_bytes(b"1")
    second.write_bytes(b"2")

    _submit_and_cancel(
        core_env,
        fake_core,
        "core",
        "detect",
        "video-batch",
        "--files",
        str(first),
        "--files",
        str(second),
    )

    batches = _requests(fake_core, "batch")
    assert batches[0]["path"] == "/api/v1/skills/video-detect/batch"
    assert batches[0]["body"]["items"] == [
        {"id": "one", "video_uri": "/data/video/one.mp4"},
        {"id": "two", "video_uri": "/data/video/two.mp4"},
    ]
