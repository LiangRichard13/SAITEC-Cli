"""core 命令共享层：配置加载、CoreOps 构造、错误映射、输出渲染。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

import typer
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from ....core.config import load_config_with_overrides
from ....core.models import ConfigValidationError
from ....core_api.client import CoreClient
from ....core_api.errors import CoreApiError, CoreApiErrorKind
from ....runtime.core_ops import CoreOps
from ..._common import (
    EXIT_DETECTOR_ERROR,
    EXIT_INTERNAL_ERROR,
    EXIT_RUNTIME_ERROR,
    EXIT_USER_ERROR,
    console,
    emit,
    err_console,
    format_errors,
    get_config_path,
)

ActionResult = Callable[[CoreOps], Awaitable[Any]]


def _exit_code(kind: CoreApiErrorKind) -> int:
    if kind in (
        CoreApiErrorKind.CONFIG,
        CoreApiErrorKind.BUSINESS,
        CoreApiErrorKind.PAYLOAD,
        CoreApiErrorKind.NOT_FOUND,
        CoreApiErrorKind.TASK_FAILED,
    ):
        return EXIT_USER_ERROR
    if kind is CoreApiErrorKind.IO:
        return EXIT_RUNTIME_ERROR
    return EXIT_DETECTOR_ERROR


def _error_body(exc: CoreApiError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": exc.kind.value, "message": str(exc)}
    if exc.status is not None:
        body["status"] = exc.status
    if exc.retry_after:
        body["retry_after"] = exc.retry_after
    return body


def run_core_command(
    ctx: typer.Context,
    config_path: Path | None,
    json_output: bool,
    action: ActionResult,
    *,
    human: Callable[[Any], None] | None = None,
    ok_check: Callable[[Any], tuple[bool, dict[str, Any] | None]] | None = None,
) -> None:
    """加载配置 → 构造 CoreClient/CoreOps → 执行 action → 统一输出/退出码。"""
    try:
        json_output = json_output or bool(ctx.obj.get("json_output"))
    except (AttributeError, TypeError):
        pass
    path = config_path.expanduser().resolve() if config_path else get_config_path(ctx)
    try:
        config, _ = load_config_with_overrides(path)
    except FileNotFoundError:
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "CONFIG_NOT_FOUND", "message": f"config.json 不存在: {path}"},
            exit_code=EXIT_USER_ERROR,
        )
        return
    except ConfigValidationError as exc:
        emit(
            json_output=json_output,
            ok=False,
            error={
                "code": "CONFIG_VALIDATION_ERROR",
                "message": "配置校验失败",
                "errors": format_errors(exc.errors),
            },
            exit_code=EXIT_USER_ERROR,
        )
        return
    except (ValueError, KeyError) as exc:
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "CONFIG_PARSE_ERROR", "message": str(exc)},
            exit_code=EXIT_USER_ERROR,
        )
        return

    try:
        client = CoreClient(
            config.detector.url,
            config.detector.api_key,
            timeout_sec=float(config.detector.timeout_sec),
        )
    except CoreApiError as exc:
        emit(
            json_output=json_output,
            ok=False,
            error=_error_body(exc),
            exit_code=_exit_code(exc.kind),
        )
        return
    ops = CoreOps(client, download_dir=path.parent / "downloads")

    async def _run() -> Any:
        async with client:
            return await action(ops)

    try:
        data = asyncio.run(_run())
    except CoreApiError as exc:
        payload = exc.detail if exc.kind in (
            CoreApiErrorKind.TASK_FAILED,
            CoreApiErrorKind.TIMEOUT,
        ) else None
        emit(
            json_output=json_output,
            ok=False,
            data=payload,
            error=_error_body(exc),
            exit_code=_exit_code(exc.kind),
        )
        return
    except ValueError as exc:  # payload 构建/互斥校验
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "INVALID_ARGUMENT", "message": str(exc)},
            exit_code=EXIT_USER_ERROR,
        )
        return
    except Exception as exc:  # 未预期异常：不裸抛 traceback
        emit(
            json_output=json_output,
            ok=False,
            error={
                "code": "INTERNAL_ERROR",
                "message": f"{type(exc).__name__}: {exc}",
            },
            exit_code=EXIT_INTERNAL_ERROR,
        )
        return

    if ok_check is not None:
        ok, error = ok_check(data)
        if not ok:
            emit(
                json_output=json_output,
                ok=False,
                data=data,
                error=error,
                exit_code=EXIT_DETECTOR_ERROR,
            )
            return

    if json_output:
        emit(json_output=True, data=data)
    elif human is not None:
        try:
            human(data)
        except Exception as exc:  # 渲染失败也不能裸抛 traceback
            emit(
                json_output=False,
                ok=False,
                error={
                    "code": "RENDER_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                },
                exit_code=EXIT_INTERNAL_ERROR,
            )
    else:
        emit(json_output=False, data=data)


def progress_callback(
    task_id: str, json_output: bool
) -> Callable[[dict[str, Any]], None]:
    last = {"status": None}

    def callback(task: dict[str, Any]) -> None:
        status = task.get("status")
        if status == last["status"]:
            return
        last["status"] = status
        if json_output or not sys.stderr.isatty():
            print(
                json.dumps(
                    {"task_id": task_id, "status": status}, ensure_ascii=False
                ),
                file=sys.stderr,
            )
        else:
            err_console.print(
                f"[dim]任务 {escape(str(task_id))}: {escape(str(status))}[/dim]"
            )

    return callback


async def submit_and_maybe_wait(
    ops: CoreOps,
    data: Any,
    *,
    wait: bool,
    interval: float,
    timeout: float,
    json_output: bool,
) -> Any:
    """创建类命令：默认返回 running；--wait 时轮询到终态。"""
    if not wait:
        return data
    task_id = data.get("local_task_id") if isinstance(data, dict) else None
    if not task_id:
        raise CoreApiError(
            CoreApiErrorKind.BUSINESS, "后端未返回 local_task_id", detail=data
        )
    return await ops.task_wait(
        str(task_id),
        interval=interval,
        timeout=timeout,
        on_status=progress_callback(str(task_id), json_output),
    )


# ---- 人类可读渲染 ----


def _cell(value: Any) -> Text:
    return Text("-" if value is None else str(value))


def _escaped(value: Any) -> str:
    return escape("-" if value is None else str(value))


def render_submit(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    task_id = data.get("local_task_id") or data.get("task_id")
    console.print(f"[cyan]task_id:[/cyan] {_escaped(task_id)}")
    console.print(f"[dim]status:[/dim] {_escaped(data.get('status', 'unknown'))}")
    if data.get("message"):
        console.print(f"[dim]{escape(str(data['message']))}[/dim]")


def render_task(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    console.print(f"[cyan]task_id:[/cyan] {_escaped(data.get('local_task_id', '-'))}")
    console.print(f"[dim]task_type:[/dim] {_escaped(data.get('task_type', '-'))}")
    console.print(f"[dim]status:[/dim] {_escaped(data.get('status', '-'))}")
    if data.get("error_message"):
        console.print(f"[red]error:[/red] {_escaped(data['error_message'])}")
    if data.get("created_at"):
        console.print(f"[dim]created:[/dim] {_escaped(data['created_at'])}")
    if data.get("completed_at"):
        console.print(f"[dim]completed:[/dim] {_escaped(data['completed_at'])}")
    if data.get("output_oversize"):
        console.print(
            "[yellow]output_payload 过大已省略"
            f"（{_escaped(data.get('output_size_bytes'))} bytes）；"
            "请用 `saitec-cli core task artifacts <task_id>` 查看产物[/yellow]"
        )
    output = data.get("output_payload")
    if output:
        console.print("[dim]output_payload:[/dim]")
        console.print(json.dumps(output, ensure_ascii=False, indent=2), markup=False)


def render_task_list(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    tasks = data.get("tasks") or []
    if not tasks:
        console.print("[dim]暂无任务[/dim]")
        return
    table = Table(title=f"任务列表（{len(tasks)} 条）", title_style="cyan bold")
    table.add_column("task_id", no_wrap=True)
    table.add_column("名称")
    table.add_column("类型")
    table.add_column("状态")
    table.add_column("创建时间")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        payload = task.get("input_payload")
        task_name = task.get("task_name")
        if not task_name and isinstance(payload, dict):
            task_name = payload.get("task_name")
        table.add_row(
            _cell(task.get("local_task_id")),
            _cell(task_name),
            _cell(task.get("task_type")),
            _cell(task.get("status")),
            _cell(task.get("created_at")),
        )
    console.print(table)
    console.print(f"[dim]total: {_escaped(data.get('total', len(tasks)))}[/dim]")


def render_files(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    files = data.get("files") or []
    if not files:
        console.print("[dim]暂无文件[/dim]")
        return
    table = Table(title=f"文件列表（{len(files)} 条）", title_style="cyan bold")
    table.add_column("file_id", no_wrap=True)
    table.add_column("role")
    table.add_column("文件名")
    table.add_column("大小")
    table.add_column("storage_uri")
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        if not filename and item.get("storage_uri"):
            filename = Path(str(item["storage_uri"])).name
        table.add_row(
            _cell(item.get("file_id")),
            _cell(item.get("role", item.get("file_type"))),
            _cell(filename),
            _cell(item.get("size_bytes")),
            _cell(item.get("storage_uri")),
        )
    console.print(table)
    if "total" in data:
        console.print(f"[dim]total: {_escaped(data['total'])}[/dim]")


def render_models(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    models = data.get("models") or []
    if models:
        table = Table(title="被测模型", title_style="cyan bold")
        table.add_column("model_id", no_wrap=True)
        table.add_column("名称")
        table.add_column("base_url")
        table.add_column("api_key")
        for item in models:
            if isinstance(item, dict):
                table.add_row(
                    _cell(item.get("id")),
                    _cell(item.get("model_name")),
                    _cell(item.get("base_url")),
                    _cell(item.get("api_key")),
                )
        console.print(table)
    else:
        console.print("[dim]暂无自有被测模型[/dim]")

    platform = data.get("platform_models") or []
    if platform:
        table = Table(title="平台模型目录", title_style="cyan bold")
        table.add_column("model_id", no_wrap=True)
        table.add_column("模型")
        table.add_column("价格(分/百次)")
        table.add_column("币种")
        table.add_column("角色")
        for item in platform:
            if isinstance(item, dict):
                table.add_row(
                    _cell(item.get("id")),
                    _cell(item.get("llm_model")),
                    _cell(item.get("unit_price_amount")),
                    _cell(item.get("currency")),
                    _cell(item.get("model_role")),
                )
        console.print(table)


def render_status(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    console.print(f"[cyan]base_url:[/cyan] {_escaped(data.get('base_url'))}")
    console.print(f"[dim]api_key_set:[/dim] {_escaped(data.get('api_key_set'))}")
    console.print(f"[dim]health_ok:[/dim] {_escaped(data.get('health_ok'))}")
    console.print(f"[dim]auth_ok:[/dim] {_escaped(data.get('auth_ok'))}")
    if data.get("task_count") is not None:
        console.print(f"[dim]任务数:[/dim] {_escaped(data['task_count'])}")
    for key in ("health_error", "auth_error"):
        if data.get(key):
            console.print(f"[red]{key}:[/red] {_escaped(data[key])}")


def render_upload(data: Any) -> None:
    render_submit_meta(data)


def render_submit_meta(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    for key in ("file_id", "filename", "size_bytes", "sha256", "storage_uri", "file_type"):
        if data.get(key) is not None:
            console.print(f"[dim]{key}:[/dim] {_escaped(data[key])}")


def render_download(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    console.print(f"[green]已保存:[/green] {_escaped(data.get('path'))}")
    console.print(f"[dim]size_bytes:[/dim] {_escaped(data.get('size_bytes'))}")


def render_artifacts(data: Any) -> None:
    render_files(data)


def render_cancel(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    console.print(f"[dim]task_id:[/dim] {_escaped(data.get('task_id'))}")
    console.print(f"[dim]cancelled:[/dim] {_escaped(data.get('cancelled'))}")
    console.print(f"[dim]status:[/dim] {_escaped(data.get('status'))}")
    console.print(f"[dim]reason:[/dim] {_escaped(data.get('reason'))}")


def render_credentials(data: Any) -> None:
    if not isinstance(data, dict):
        console.print(str(data), markup=False)
        return
    console.print("[green]凭证已注入[/green]")
    console.print(f"[dim]env_name:[/dim] {_escaped(data.get('env_name'))}")
    console.print(f"[dim]model_name:[/dim] {_escaped(data.get('model_name'))}")
    console.print(f"[dim]source:[/dim] {_escaped(data.get('source'))}")


def render_content(data: Any) -> None:
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        console.print(data["content"], markup=False)
    else:
        console.print(str(data), markup=False)
