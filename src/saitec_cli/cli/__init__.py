"""cli — Layer 6

命令行入口（typer）。命令与子命令组清单以 `saitec-cli --help` 为准。
"""
from .main import app

__all__ = ["app"]