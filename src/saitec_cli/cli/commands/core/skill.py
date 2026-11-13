"""core skill — 本地技能文档的 list/get/search（MCP 技能文档工具的 CLI 等价）。"""
from __future__ import annotations

import os
from pathlib import Path

import typer

from rich.markup import escape

from ..._common import EXIT_USER_ERROR, console, emit

app = typer.Typer(help="本地技能文档检索")


def _default_dir() -> Path:
    env = os.environ.get("SAITEC_CLI_SKILL_DIR")
    if env:
        return Path(env).expanduser()
    # src/saitec_cli/cli/commands/core/skill.py -> repo/skills/saitec-cli
    return Path(__file__).resolve().parents[5] / "skills" / "saitec-cli"


def _resolve_dir(path: Path | None) -> Path:
    root = path.expanduser() if path else _default_dir()
    if not root.is_dir():
        raise ValueError(
            f"技能文档目录不存在: {root}；可用 --dir 或 SAITEC_CLI_SKILL_DIR 指定"
        )
    return root


def _frontmatter(text: str) -> tuple[str | None, str | None]:
    if not text.startswith("---"):
        return None, None
    end = text.find("\n---", 3)
    if end < 0:
        return None, None
    name = description = None
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key.strip() == "name":
            name = value.strip().strip("'\"") or None
        elif key.strip() == "description":
            description = value.strip().strip("'\"") or None
    return name, description


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _iter_docs(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def _effective_json(ctx: typer.Context, json_output: bool) -> bool:
    try:
        return json_output or bool(ctx.obj.get("json_output"))
    except (AttributeError, TypeError):
        return json_output


@app.command("list")
def list_skills_cmd(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json"),
    dir_path: Path | None = typer.Option(None, "--dir", help="技能文档目录"),
) -> None:
    """📚 列出本地技能文档"""
    json_output = _effective_json(ctx, json_output)
    try:
        root = _resolve_dir(dir_path)
    except ValueError as exc:
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "SKILL_DIR_NOT_FOUND", "message": str(exc)},
            exit_code=EXIT_USER_ERROR,
        )
        return
    skills = []
    for path in _iter_docs(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        name, description = _frontmatter(text)
        rel = path.relative_to(root).with_suffix("")
        skills.append(
            {
                "name": name or str(rel),
                "path": str(path),
                "description": description or _first_heading(text) or "",
            }
        )
    if json_output:
        emit(json_output=True, data={"dir": str(root), "skills": skills})
        return
    if not skills:
        console.print("[dim]暂无技能文档[/dim]")
        return
    for item in skills:
        console.print(
            f"[cyan]{escape(str(item['name']))}[/cyan] "
            f"[dim]({escape(str(item['path']))})[/dim]\n  "
            f"{escape(str(item['description']))}"
        )


def _find_doc(root: Path, name: str) -> Path | None:
    target = name.strip()
    for path in _iter_docs(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        fm_name, _ = _frontmatter(text)
        rel = str(path.relative_to(root).with_suffix(""))
        if target in (fm_name, rel, path.name, path.stem):
            return path
    return None


@app.command("get")
def get_skill_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="技能名 / 相对路径 / 文件名"),
    json_output: bool = typer.Option(False, "--json"),
    dir_path: Path | None = typer.Option(None, "--dir"),
) -> None:
    """📄 输出指定技能文档全文"""
    json_output = _effective_json(ctx, json_output)
    try:
        root = _resolve_dir(dir_path)
    except ValueError as exc:
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "SKILL_DIR_NOT_FOUND", "message": str(exc)},
            exit_code=EXIT_USER_ERROR,
        )
        return
    path = _find_doc(root, name)
    if path is None:
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "SKILL_NOT_FOUND", "message": f"技能文档不存在: {name}"},
            exit_code=EXIT_USER_ERROR,
        )
        return
    content = path.read_text(encoding="utf-8", errors="replace")
    if json_output:
        emit(
            json_output=True,
            data={"name": name, "path": str(path), "content": content},
        )
    else:
        console.print(content, markup=False)


@app.command("search")
def search_skills_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="搜索关键词"),
    json_output: bool = typer.Option(False, "--json"),
    dir_path: Path | None = typer.Option(None, "--dir"),
) -> None:
    """🔎 在所有本地技能文档中搜索关键词"""
    json_output = _effective_json(ctx, json_output)
    try:
        root = _resolve_dir(dir_path)
    except ValueError as exc:
        emit(
            json_output=json_output,
            ok=False,
            error={"code": "SKILL_DIR_NOT_FOUND", "message": str(exc)},
            exit_code=EXIT_USER_ERROR,
        )
        return
    needle = query.lower()
    matches = []
    for path in _iter_docs(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                matches.append(
                    {
                        "file": str(path.relative_to(root)),
                        "line": lineno,
                        "text": line.strip()[:300],
                    }
                )
                if len(matches) >= 200:
                    break
        if len(matches) >= 200:
            break
    if json_output:
        emit(json_output=True, data={"query": query, "matches": matches})
        return
    if not matches:
        console.print("[dim]无匹配[/dim]")
        return
    for m in matches:
        console.print(
            f"[cyan]{escape(str(m['file']))}:{escape(str(m['line']))}[/cyan] "
            f"{escape(str(m['text']))}"
        )
