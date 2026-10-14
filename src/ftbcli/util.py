"""FTBCLI 内部共用的小工具。"""

from __future__ import annotations

from pathlib import Path

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_SIZE_UNITS = "KMGTP"


def display_path(path: Path) -> str:
    """尽量显示相对当前目录的短路径。"""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def human_size(size: float) -> str:
    """把字节数变成人类可读的字符串。"""
    index = 0
    while size >= 1024 and index < len(_UNITS) - 1:
        size /= 1024
        index += 1
    return (
        f"{int(size)} {_UNITS[index]}" if index == 0 else f"{size:.1f} {_UNITS[index]}"
    )


def parse_size(text: str) -> int:
    """解析 ``1024`` / ``512K`` / ``1M`` / ``2G`` 之类的字节数。"""
    raw = text.strip().upper()
    raw = raw.removesuffix("B")
    multiplier = 1
    if raw and raw[-1] in _SIZE_UNITS:
        multiplier = 1024 ** (_SIZE_UNITS.index(raw[-1]) + 1)
        raw = raw[:-1]
    if not raw.isdigit():
        raise ValueError(f"无法解析大小：{text}")
    return int(raw) * multiplier
