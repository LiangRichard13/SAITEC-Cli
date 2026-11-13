"""core credentials — 运行时凭证注入。"""
from __future__ import annotations

from pathlib import Path

import typer

from ._shared import render_credentials, run_core_command

app = typer.Typer(help="按 model_id 注入评测运行时凭证")

_SKILLS = ("safety-eval", "general-eval", "corpus-safety-eval")


@app.command("set")
def set_credentials_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    skill: str = typer.Option(..., "--skill", help="safety-eval/general-eval/corpus-safety-eval"),
    env_name: str = typer.Option(..., "--env-name", help="下游环境变量名，如 DEEPSEEK_API_KEY"),
    model_id: str = typer.Option(..., "--model-id", help="被测模型 UUID（来自 core models list）"),
    overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite"),
) -> None:
    """🔑 按 model_id 注入凭证（不传明文 API Key）

    Core 会根据 model_id 解析自有模型或平台模型，并把密钥注入下游评测服务。
    平台模型余额不足时返回 402。
    """
    async def action(ops):
        if skill not in _SKILLS:
            raise ValueError(f"skill 必须是 {list(_SKILLS)}")
        return await ops.inject_credentials(
            skill, env_name=env_name, model_id=model_id, overwrite=overwrite
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_credentials
    )
