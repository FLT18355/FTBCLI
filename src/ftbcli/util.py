"""FTBCLI 内部共用的小工具。"""

from __future__ import annotations

from pathlib import Path

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_SIZE_UNITS = "KMGTP"


def find_executable(*names: str) -> str | None:
    """返回第一个存在的可执行文件路径。"""
    from shutil import which

    for name in names:
        if (found := which(name)) is not None:
            return found
    return None


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


def split_source_output(
    paths: list[Path], output: Path | None
) -> tuple[list[Path], Path | None]:
    """拆出输入列表与「位置输出文件」。

    恰好两个参数、第一个是文件、第二个不存在且带后缀时，把第二个当成输出，
    这样 ``ftb media 输入.mp4 输出.mkv`` 能直接用；其余情况都是批量输入。
    """
    if (
        output is None
        and len(paths) == 2
        and paths[0].is_file()
        and not paths[1].exists()
        and paths[1].suffix
    ):
        return [paths[0]], paths[1]
    return list(paths), output


def require_files(paths: list[Path]) -> None:
    """校验输入都是存在的文件，否则用中文提示并退出。"""
    import typer

    for path in paths:
        if path.is_dir():
            typer.secho(
                f"错误：{display_path(path)} 是目录，请输入文件", fg="red", err=True
            )
            raise typer.Exit(2)
        if not path.exists():
            typer.secho(
                f"错误：找不到输入文件 {display_path(path)}", fg="red", err=True
            )
            raise typer.Exit(2)
