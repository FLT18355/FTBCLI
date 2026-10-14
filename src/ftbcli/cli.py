"""FTBCLI —— FLT18355 的工具箱命令行入口。

子命令按需加载：只 import 实际要用的那个工具模块，避免每次启动都白白付出
其它工具（以及它们依赖的 tarfile / zipfile / 线程池等）的导入开销。
"""

from __future__ import annotations

import importlib
import sys

import typer

#: 子命令名 → 工具模块名
TOOLS: dict[str, str] = {
    "unpack": "unpack",
    "arch": "arch",
    "hash": "hash",
    "dup": "dup",
}

app = typer.Typer(
    name="ftb",
    help="FLT18355 的工具箱（FTB）。",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


@app.callback()
def _root() -> None:
    """FTB 工具箱根命令（仅用于保留子命令层级）。"""


def _load(name: str) -> None:
    """导入并注册一个工具模块。"""
    importlib.import_module(f"ftbcli.tools.{name}").register(app)


def main() -> None:
    """命令行入口：先看第一个词是不是已知工具，是就只加载那一个。"""
    target = next((arg for arg in sys.argv[1:] if not arg.startswith("-")), None)
    if target in TOOLS:
        _load(TOOLS[target])
    else:
        for name in sorted(set(TOOLS.values())):
            _load(name)
    app()
