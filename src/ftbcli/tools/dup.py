"""ftb dup —— 查找重复文件并按需删除。

按「文件大小 → 头部 64K 哈希 → 全文哈希」逐级筛选，尽量少读盘；硬链接（同一 inode）
只算一个文件，符号链接不跟随。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Annotated

import typer

from ftbcli.util import display_path, human_size, parse_size

#: 第一轮比较读取的字节数
HEAD_BYTES = 64 * 1024


def _auto_jobs() -> int:
    """默认并行度。"""
    return min(32, (os.cpu_count() or 4) + 4)


def _digest(path: Path, limit: int | None = None) -> str | None:
    """计算哈希；limit 为 None 时读全文，读取失败返回 None。"""
    from hashlib import blake2b

    hasher = blake2b(digest_size=32)
    try:
        with path.open("rb") as fh:
            remaining = limit
            while remaining is None or remaining > 0:
                chunk = fh.read(
                    1 << 20 if remaining is None else min(1 << 20, remaining)
                )
                if not chunk:
                    break
                hasher.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
    except OSError as exc:
        typer.secho(f"跳过 {display_path(path)}：{exc}", fg="yellow", err=True)
        return None
    return hasher.hexdigest()


def _map(function, items: list[Path], workers: int) -> list[str | None]:
    """并行映射，保持顺序。"""
    if workers <= 1 or len(items) == 1:
        return [function(item) for item in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(function, items))


def _collect(roots: list[Path], min_size: int) -> dict[int, list[Path]]:
    """按大小收集候选文件（同一 inode 只留一个）。"""
    by_size: dict[int, list[Path]] = {}
    seen: set[tuple[int, int]] = set()
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath, name)
                try:
                    info = path.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_size < min_size:
                    continue
                key = (info.st_dev, info.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                by_size.setdefault(info.st_size, []).append(path)
    return by_size


def _group_by_digest(
    paths: list[Path], workers: int, limit: int | None
) -> list[list[Path]]:
    """按哈希分组，返回其中数量大于 1 的组。"""
    digests = _map(lambda path: _digest(path, limit), paths, workers)
    buckets: dict[str, list[Path]] = {}
    for path, value in zip(paths, digests, strict=True):
        if value is not None:
            buckets.setdefault(value, []).append(path)
    return [sorted(group) for group in buckets.values() if len(group) > 1]


def find_duplicates(
    roots: list[Path], min_size: int, workers: int
) -> list[tuple[int, list[Path]]]:
    """返回 (单文件大小, 重复文件组)，按可释放空间从大到小排序。"""
    groups: list[tuple[int, list[Path]]] = []
    for size, candidates in _collect(roots, min_size).items():
        if len(candidates) < 2:
            continue
        for subset in _group_by_digest(candidates, workers, HEAD_BYTES):
            if size <= HEAD_BYTES:
                groups.append((size, subset))
                continue
            for group in _group_by_digest(subset, workers, None):
                groups.append((size, group))
    groups.sort(key=lambda item: item[0] * (len(item[1]) - 1), reverse=True)
    return groups


def dup(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="要扫描的目录，默认当前目录", file_okay=False, exists=True),
    ] = None,
    min_size: Annotated[
        str, typer.Option("--min-size", "-m", help="只考虑不小于该大小的文件，如 1M")
    ] = "1",
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="并行数，0 表示自动")] = 0,
    delete: Annotated[
        bool, typer.Option("--delete", help="删除多余副本（每组保留第一个）")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="删除时不再确认")] = False,
) -> None:
    """查找重复文件，可确认后删除多余副本。"""
    try:
        threshold = parse_size(min_size)
    except ValueError as exc:
        typer.secho(f"错误：{exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    roots = paths or [Path()]
    workers = jobs if jobs > 0 else _auto_jobs()
    groups = find_duplicates(roots, threshold, workers)

    if not groups:
        typer.secho("没有发现重复文件", fg="green")
        return

    waste = sum(size * (len(group) - 1) for size, group in groups)
    typer.secho(
        f"发现 {len(groups)} 组重复文件，共 {sum(len(g) for _, g in groups)} 个，可释放 {human_size(waste)}",
        fg="cyan",
        bold=True,
    )
    for index, (size, group) in enumerate(groups, 1):
        typer.secho(
            f"\n第 {index} 组（{human_size(size)} × {len(group)}，可省 {human_size(size * (len(group) - 1))}）"
        )
        for offset, path in enumerate(group):
            label = "保留" if offset == 0 else "重复"
            colour = "green" if offset == 0 else "yellow"
            typer.secho(f"  {label} {display_path(path)}", fg=colour)

    if not delete:
        typer.secho("\n加 --delete 可删除每组中标记为重复的文件", fg="bright_black")
        return

    doomed = [path for _, group in groups for path in group[1:]]
    typer.secho(
        f"\n将删除 {len(doomed)} 个文件，释放 {human_size(waste)}", fg="red", bold=True
    )
    for path in doomed:
        typer.echo(f"  删除 {display_path(path)}")

    if not yes and not typer.confirm("确认删除？", default=False):
        typer.secho("已取消", fg="yellow")
        return

    removed = 0
    freed = 0
    for size, group in groups:
        for path in group[1:]:
            try:
                path.unlink()
            except OSError as exc:
                typer.secho(f"删除失败 {display_path(path)}：{exc}", fg="red", err=True)
                continue
            removed += 1
            freed += size
    typer.secho(f"已删除 {removed} 个文件，释放 {human_size(freed)}", fg="green")


def register(app: typer.Typer) -> None:
    """把 dup 命令挂到主程序上。"""
    app.command("dup", help="查找重复文件（按大小 + 哈希逐级筛选），可确认后删除")(dup)
