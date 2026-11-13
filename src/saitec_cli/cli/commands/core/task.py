"""core task — 任务列表/详情/等待/取消/产物。"""
from __future__ import annotations

from pathlib import Path

import typer

from ....core_api.errors import CoreApiError, CoreApiErrorKind
from ._shared import (
    progress_callback,
    render_artifacts,
    render_cancel,
    render_task,
    render_task_list,
    run_core_command,
)

app = typer.Typer(help="任务查询/等待/取消/产物")


@app.command("list")
def list_tasks_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    task_type: str | None = typer.Option(None, "--task-type", help="按任务类型筛选"),
    skip: int = typer.Option(0, "--skip", min=0),
    limit: int = typer.Option(50, "--limit", min=1, max=200),
    created_start: str | None = typer.Option(None, "--created-start", help="创建时间下界 ISO8601"),
    created_end: str | None = typer.Option(None, "--created-end", help="创建时间上界 ISO8601"),
) -> None:
    """📋 查询任务列表"""

    async def action(ops):
        return await ops.task_list(
            task_type=task_type,
            skip=skip,
            limit=limit,
            created_start=created_start,
            created_end=created_end,
        )

    run_core_command(ctx, config_path, json_output, action, human=render_task_list)


@app.command("get")
def get_task_cmd(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="local_task_id"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """🔎 查询任务详情（output_payload 超 1MiB 时自动省略）"""

    async def action(ops):
        return await ops.task_get(task_id)

    run_core_command(ctx, config_path, json_output, action, human=render_task)


@app.command("wait")
def wait_task_cmd(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="local_task_id"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    interval: float = typer.Option(5.0, "--interval", min=0.1, help="轮询间隔（秒）"),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1, help="总超时（秒）"),
) -> None:
    """⏳ 轮询任务到 succeeded/failed

    成功 exit 0；failed exit 1；超时 exit 3。进度写 stderr，最终结果写 stdout。
    """

    async def action(ops):
        return await ops.task_wait(
            task_id,
            interval=interval,
            timeout=timeout,
            on_status=progress_callback(task_id, json_output),
        )

    run_core_command(ctx, config_path, json_output, action, human=render_task)


@app.command("cancel")
def cancel_task_cmd(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="local_task_id"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """🛑 取消运行中的任务（取消后为 failed + Cancelled by user）"""

    async def action(ops):
        data = await ops.task_cancel(task_id)
        if isinstance(data, dict) and data.get("reason") == "not_found":
            raise CoreApiError(
                CoreApiErrorKind.NOT_FOUND, f"任务不存在或无权访问: {task_id}", detail=data
            )
        return data

    run_core_command(ctx, config_path, json_output, action, human=render_cancel)


@app.command("artifacts")
def task_artifacts_cmd(
    ctx: typer.Context,
    task_id: str = typer.Argument(..., help="local_task_id"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    role: str | None = typer.Option(None, "--role", help="report/log/output/input"),
    skip: int = typer.Option(0, "--skip", min=0),
    limit: int = typer.Option(100, "--limit", min=1, max=200),
) -> None:
    """📦 查询任务产物文件（用 core file download 下载）"""

    async def action(ops):
        if role is not None and role not in ("report", "log", "output", "input"):
            raise ValueError("role 必须是 report/log/output/input")
        return await ops.task_artifacts(
            task_id, role=role, skip=skip, limit=limit
        )

    run_core_command(ctx, config_path, json_output, action, human=render_artifacts)
