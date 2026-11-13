"""core detect — 文本/图像/视频 AIGC 检测。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from ._payloads import (
    build_image_batch_payload,
    build_image_payload,
    build_text_batch_payload,
    build_text_detect_payload,
    build_video_batch_payload,
    build_video_payload,
    build_dataset,
    load_items_file,
)
from ._shared import (
    render_submit,
    render_task,
    run_core_command,
    submit_and_maybe_wait,
)

app = typer.Typer(help="AIGC 检测（text/image/video）")


def _storage_uri(upload: Any) -> str:
    if not isinstance(upload, dict) or not upload.get("storage_uri"):
        raise ValueError("上传接口未返回 storage_uri")
    return str(upload["storage_uri"])


@app.command("text")
def detect_text_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    text: str | None = typer.Option(None, "--text", help="待检测文本"),
    text_file: Path | None = typer.Option(None, "--text-file", help="从文件读取文本"),
    method: str | None = typer.Option(None, "--method", help="检测方法，默认 sample"),
    threshold: float | None = typer.Option(None, "--threshold", help="判定阈值"),
    language: str | None = typer.Option(None, "--language", help="文本语言，默认 zh"),
    task_name: str | None = typer.Option(None, "--task-name"),
    wait: bool = typer.Option(False, "--wait", help="轮询到终态"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🔤 单条文本 AIGC 检测（后台任务）"""

    async def action(ops):
        content = text
        if content and text_file:
            raise ValueError("--text 与 --text-file 只能二选一")
        if text_file:
            p = text_file.expanduser()
            if not p.is_file():
                raise ValueError(f"文件不存在: {p}")
            content = p.read_text(encoding="utf-8-sig")
        if not content or not content.strip():
            raise ValueError("需要 --text 或 --text-file")
        payload = build_text_detect_payload(
            text=content,
            method=method,
            threshold=threshold,
            language=language,
            task_name=task_name,
        )
        data = await ops.submit_detection("text-detect", payload)
        return await submit_and_maybe_wait(
            ops,
            data,
            wait=wait,
            interval=interval,
            timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("text-batch")
def detect_text_batch_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    items: Path | None = typer.Option(None, "--items", help="items JSON 文件（object 数组）"),
    dataset_file: Path | None = typer.Option(None, "--dataset-file", help="上传本地 dataset 后自动构造 dataset"),
    dataset_path: str | None = typer.Option(None, "--dataset-path", help="已上传文件的 storage_uri"),
    dataset_source_type: str = typer.Option("server_path", "--dataset-source-type"),
    dataset_file_format: str | None = typer.Option(None, "--dataset-file-format", help="json/jsonl（缺省按扩展名）"),
    dataset_data_format: str = typer.Option("case_list", "--dataset-data-format", help="prompt_list/case_list"),
    method: str | None = typer.Option(None, "--method"),
    threshold: float | None = typer.Option(None, "--threshold"),
    task_name: str | None = typer.Option(None, "--task-name"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🔤 批量文本检测（items 与 dataset 二选一）"""

    async def action(ops):
        modes = [bool(items), bool(dataset_file), bool(dataset_path)]
        if sum(modes) != 1:
            raise ValueError("--items / --dataset-file / --dataset-path 必须且只能给一个")
        if dataset_file and dataset_source_type != "server_path":
            raise ValueError("--dataset-source-type 只对 --dataset-path 生效")
        item_list = load_items_file(items) if items else None
        dataset = None
        if dataset_file:
            _, dataset = await ops.upload_dataset(
                dataset_file,
                file_format=dataset_file_format,
                data_format=dataset_data_format,
            )
        elif dataset_path:
            dataset = build_dataset(
                dataset_path,
                source_type=dataset_source_type,
                file_format=dataset_file_format,
                data_format=dataset_data_format,
            )
        payload = build_text_batch_payload(
            method=method,
            threshold=threshold,
            task_name=task_name,
            items=item_list,
            dataset=dataset,
        )
        data = await ops.submit_batch_detection("text-detect", payload)
        return await submit_and_maybe_wait(
            ops,
            data,
            wait=wait,
            interval=interval,
            timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("image")
def detect_image_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    file: Path | None = typer.Option(None, "--file", help="本地图片（自动上传）"),
    image_uri: str | None = typer.Option(None, "--image-uri", help="已上传图片的 storage_uri"),
    method: str | None = typer.Option(None, "--method", help="默认 deepfake_defenders"),
    threshold: float | None = typer.Option(None, "--threshold"),
    return_visuals: bool = typer.Option(
        True, "--return-visuals/--no-return-visuals"
    ),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🖼️ 单图 AIGC 检测（--file 自动上传）"""

    async def action(ops):
        if file and image_uri:
            raise ValueError("--file 与 --image-uri 只能二选一")
        if not file and not image_uri:
            raise ValueError("需要 --file 或 --image-uri")
        uri = image_uri
        if file:
            uri = _storage_uri(await ops.upload_file(file, "image"))
        payload = build_image_payload(
            image_uri=str(uri),
            method=method,
            threshold=threshold,
            return_visuals=return_visuals,
        )
        data = await ops.submit_detection("image-detect", payload)
        return await submit_and_maybe_wait(
            ops,
            data,
            wait=wait,
            interval=interval,
            timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("image-batch")
def detect_image_batch_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    items: Path | None = typer.Option(None, "--items", help="items JSON 文件"),
    files: list[Path] = typer.Option([], "--files", help="本地图片，可重复"),
    method: str | None = typer.Option(None, "--method", help="默认 tamper_yolo"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🖼️ 批量图片检测（--items 或 --files）"""

    async def action(ops):
        if items and files:
            raise ValueError("--items 与 --files 只能二选一")
        if not items and not files:
            raise ValueError("需要 --items 或 --files")
        item_list = load_items_file(items) if items else []
        if files:
            for path in files:
                uri = _storage_uri(await ops.upload_file(path, "image"))
                item_list.append({"id": path.stem, "image_uri": uri})
        payload = build_image_batch_payload(method=method, items=item_list)
        data = await ops.submit_batch_detection("image-detect", payload)
        return await submit_and_maybe_wait(
            ops,
            data,
            wait=wait,
            interval=interval,
            timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("video")
def detect_video_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    file: Path | None = typer.Option(None, "--file", help="本地视频（自动上传）"),
    video_uri: str | None = typer.Option(None, "--video-uri", help="已上传视频的 storage_uri"),
    method: str | None = typer.Option(None, "--method", help="默认 video_multihead_attention"),
    threshold: float | None = typer.Option(None, "--threshold"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🎬 单视频 AIGC/DeepFake 检测（--file 自动上传）"""

    async def action(ops):
        if file and video_uri:
            raise ValueError("--file 与 --video-uri 只能二选一")
        if not file and not video_uri:
            raise ValueError("需要 --file 或 --video-uri")
        uri = video_uri
        if file:
            uri = _storage_uri(await ops.upload_file(file, "video"))
        payload = build_video_payload(
            video_uri=str(uri), method=method, threshold=threshold
        )
        data = await ops.submit_detection("video-detect", payload)
        return await submit_and_maybe_wait(
            ops,
            data,
            wait=wait,
            interval=interval,
            timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("video-batch")
def detect_video_batch_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    items: Path | None = typer.Option(None, "--items", help="items JSON 文件"),
    files: list[Path] = typer.Option([], "--files", help="本地视频，可重复"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🎬 批量视频检测（--items 或 --files）"""

    async def action(ops):
        if items and files:
            raise ValueError("--items 与 --files 只能二选一")
        if not items and not files:
            raise ValueError("需要 --items 或 --files")
        item_list = load_items_file(items) if items else []
        if files:
            for path in files:
                uri = _storage_uri(await ops.upload_file(path, "video"))
                item_list.append({"id": path.stem, "video_uri": uri})
        payload = build_video_batch_payload(items=item_list)
        data = await ops.submit_batch_detection("video-detect", payload)
        return await submit_and_maybe_wait(
            ops,
            data,
            wait=wait,
            interval=interval,
            timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )
