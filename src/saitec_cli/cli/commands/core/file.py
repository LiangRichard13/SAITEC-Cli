"""core file — 上传、下载、列表、文本读取。"""
from __future__ import annotations

from pathlib import Path

import typer

from ....core_api.endpoints import VALID_FILE_TYPES
from ._shared import (
    render_content,
    render_download,
    render_files,
    render_upload,
    run_core_command,
)

app = typer.Typer(help="Core 文件上传/下载/列表/读取")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
_DATASET_EXTS = {".json", ".jsonl", ".csv", ".tsv", ".txt"}
_REPORT_EXTS = {".md", ".html", ".pdf"}
_LOG_EXTS = {".log"}


def infer_file_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _DATASET_EXTS:
        return "dataset"
    if ext in _REPORT_EXTS:
        return "report"
    if ext in _LOG_EXTS:
        return "log"
    raise ValueError(f"无法根据扩展名推断 file_type，请用 --type 指定: {path}")


@app.command("upload")
def upload_cmd(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="本地文件路径"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    file_type: str | None = typer.Option(
        None, "--type", help="image/video/dataset/output/log/report（缺省按扩展名推断）"
    ),
    timeout: float = typer.Option(300.0, "--timeout", min=1.0, help="上传读超时（秒）"),
) -> None:
    """⬆️ 上传本地文件到 Core

    返回 file_id / storage_uri / sha256 / size_bytes；图片/视频检测和评测
    dataset 可直接使用返回的 storage_uri。
    """

    async def action(ops):
        if file_type is not None and file_type not in VALID_FILE_TYPES:
            raise ValueError(f"--type 必须是 {list(VALID_FILE_TYPES)}")
        resolved_type = file_type or infer_file_type(path)
        return await ops.upload_file(path, resolved_type, timeout=timeout)

    run_core_command(ctx, config_path, json_output, action, human=render_upload)


@app.command("download")
def download_cmd(
    ctx: typer.Context,
    file_id: str = typer.Argument(..., help="文件 UUID"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "-o", "--output", help="保存路径（文件，或以 / 结尾的目录）"),
    overwrite: bool = typer.Option(False, "--overwrite", help="覆盖已存在的目标文件"),
    timeout: float = typer.Option(300.0, "--timeout", min=1.0, help="下载读超时（秒）"),
) -> None:
    """⬇️ 下载 Core 文件到本地

    默认保存到 `{config_dir}/downloads/<服务端文件名>`；先写 `.part` 再原子替换。
    """

    async def action(ops):
        return await ops.download_file(
            file_id,
            dest=output,
            overwrite=overwrite,
            timeout=timeout,
        )

    run_core_command(ctx, config_path, json_output, action, human=render_download)


@app.command("list")
def list_files_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    skip: int = typer.Option(0, "--skip", min=0),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    """📁 列出当前用户的私有文件（不含任务产物）"""

    async def action(ops):
        return await ops.list_files(skip=skip, limit=limit)

    run_core_command(ctx, config_path, json_output, action, human=render_files)


@app.command("read")
def read_file_cmd(
    ctx: typer.Context,
    file_id: str = typer.Argument(..., help="文件 UUID"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """📄 读取文本文件内容（.json/.jsonl/.txt/.csv/.log/.md/.yaml/.yml/.xml）"""

    async def action(ops):
        return await ops.read_file(file_id)

    run_core_command(ctx, config_path, json_output, action, human=render_content)
