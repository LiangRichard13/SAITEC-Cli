"""core 命令组：直接调用 SAITEC Core 平台。"""
from __future__ import annotations

import typer

from . import credentials, detect, eval, file, models, skill, status, task

app = typer.Typer(
    help="SAITEC Core 平台操作（检测/评测/文件/任务/模型）",
    no_args_is_help=True,
)

app.command("status")(status.status_cmd)
app.add_typer(models.app, name="models")
app.add_typer(credentials.app, name="credentials")
app.add_typer(file.app, name="file")
app.add_typer(detect.app, name="detect")
app.add_typer(eval.app, name="eval")
app.add_typer(task.app, name="task")
app.add_typer(skill.app, name="skill")
