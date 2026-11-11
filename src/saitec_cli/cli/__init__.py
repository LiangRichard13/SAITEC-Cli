"""cli — Layer 6

命令行入口（typer）。13 个命令详见 `docs/design/saitec-cli-design.md` §13。
"""
from .main import app

__all__ = ["app"]