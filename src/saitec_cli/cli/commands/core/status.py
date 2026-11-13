"""core status — 平台连接与鉴权检查。"""
from __future__ import annotations

from pathlib import Path

import typer

from ._shared import render_status, run_core_command


def status_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """🔌 检查 Core 平台地址、Key、健康状态与任务数

    使用 `detector.url` + `detector.api_key`（同一套平台连接）。不要求本地
    代理正在运行。

    \b
    Examples:
      saitec-cli core status
      saitec-cli core status --json
    """

    def _check(data: dict) -> tuple[bool, dict | None]:
        if data.get("health_ok") and data.get("auth_ok"):
            return True, None
        message = data.get("auth_error") or data.get("health_error") or "平台状态异常"
        return False, {"code": "STATUS_FAILED", "message": message}

    run_core_command(
        ctx,
        config_path,
        json_output,
        lambda ops: ops.status(),
        human=render_status,
        ok_check=_check,
    )
