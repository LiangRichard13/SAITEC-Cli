"""core models — 被测模型与平台模型目录。"""
from __future__ import annotations

from pathlib import Path

import typer

from ._shared import render_models, run_core_command

app = typer.Typer(help="被测模型与平台模型目录")


@app.command("list")
def list_models_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    skip: int = typer.Option(0, "--skip", min=0),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    """📋 列出当前用户被测模型与平台模型目录

    自有模型的 api_key 在输出中打码；平台模型按“分/百次调用”计费。
    """

    async def action(ops):
        return await ops.list_models(skip=skip, limit=limit)

    run_core_command(ctx, config_path, json_output, action, human=render_models)
