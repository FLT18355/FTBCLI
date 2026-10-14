"""ftb hash —— 计算与校验文件校验和。

输出格式与 ``sha256sum`` 一致（``<哈希>  <路径>``），可直接重定向成校验文件再用
``ftb hash -c`` 复核。
"""

from __future__ import annotations

# hashlib 与线程池延迟到真正计算时再导入，缩短 CLI 启动时间
import os
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from ftbcli.util import display_path

CHUNK = 1 << 20


class Algo(str, Enum):
    """可用的哈希算法。"""

    md5 = "md5"
    sha1 = "sha1"
    sha256 = "sha256"
    sha512 = "sha512"
    blake2b = "blake2b"
    blake2s = "blake2s"


#: 十六进制长度 → 可能的算法，用于提示用户 -a 是否选错
ALGO_BY_LENGTH: dict[int, tuple[str, ...]] = {
    32: ("md5",),
    40: ("sha1",),
    64: ("sha256", "blake2s"),
    128: ("sha512", "blake2b"),
}


def digest(path: Path, algo: str) -> str:
    """流式计算文件哈希。"""
    import hashlib

    hasher = hashlib.new(algo)
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


def _auto_jobs() -> int:
    """默认并行度。"""
    return min(32, (os.cpu_count() or 4) + 4)


def _expand(paths: list[Path], recursive: bool) -> list[Path]:
    """展开待处理的文件列表；目录需要 -r 才递归。"""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            if recursive:
                files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
            else:
                typer.secho(
                    f"跳过目录 {display_path(path)}：加 -r 才会递归",
                    fg="yellow",
                    err=True,
                )
        else:
            files.append(path)
    return files


def _parse_manifest(manifest: Path) -> list[tuple[str, str]]:
    """解析校验清单，返回 (哈希, 相对路径) 列表。"""
    entries: list[tuple[str, str]] = []
    text = manifest.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest_text, name = parts[0], parts[1].strip().lstrip("*").strip()
        if digest_text and name:
            entries.append((digest_text.lower(), name))
    return entries


def _run_check(manifest: Path, algo: Algo) -> None:
    """校验清单模式。"""
    entries = _parse_manifest(manifest)
    if not entries:
        typer.secho(
            f"错误：{display_path(manifest)} 里没有可用的校验行", fg="red", err=True
        )
        raise typer.Exit(1)

    known = ALGO_BY_LENGTH.get(len(entries[0][0]))
    if known and algo.value not in known:
        candidates = " / ".join(known)
        typer.secho(
            f"提示：清单里的哈希是 {len(entries[0][0])} 位，可能是 {candidates}，"
            f"当前算法是 {algo.value}",
            fg="yellow",
            err=True,
        )

    base = manifest.parent if str(manifest.parent) else Path()
    missing = failed = 0
    for expected, relative in entries:
        target = base / relative
        if not target.is_file():
            # 清单里的路径是相对生成时的当前目录；找不到就按当前目录再试一次
            local = Path(relative)
            if local.is_file():
                target = local
            else:
                typer.secho(f"{relative}: 缺少文件", fg="red")
                missing += 1
                continue
        actual = digest(target, algo.value)
        if actual == expected:
            typer.secho(f"{relative}: 通过", fg="green")
        else:
            typer.secho(f"{relative}: 校验失败", fg="red")
            typer.echo(f"  期望 {expected}")
            typer.echo(f"  实际 {actual}")
            failed += 1

    total = len(entries)
    if failed or missing:
        typer.secho(
            f"\n共 {total} 项：通过 {total - failed - missing}，失败 {failed}，缺少 {missing}",
            fg="red",
        )
        raise typer.Exit(1)
    typer.secho(f"\n共 {total} 项全部通过", fg="green")


def hash_cmd(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="要计算哈希的文件或目录", exists=True),
    ] = None,
    algo: Annotated[Algo, typer.Option("--algo", "-a", help="哈希算法")] = Algo.sha256,
    recursive: Annotated[
        bool, typer.Option("--recursive", "-r", help="目录递归")
    ] = False,
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="并行数，0 表示自动")] = 0,
    check: Annotated[
        Path | None,
        typer.Option(
            "--check", "-c", help="校验模式：读取校验清单文件（与位置参数互斥）"
        ),
    ] = None,
) -> None:
    """计算文件的校验和，或用校验清单复核文件。"""
    if check is not None:
        if not check.is_file():
            typer.secho(f"错误：找不到校验清单 {check}", fg="red", err=True)
            raise typer.Exit(1)
        _run_check(check, algo)
        return

    if not paths:
        typer.secho(
            "错误：请给出文件或目录（或用 -c 指定校验清单）", fg="red", err=True
        )
        raise typer.Exit(2)

    files = _expand(paths, recursive)
    if not files:
        typer.secho("错误：没有需要计算的文件", fg="red", err=True)
        raise typer.Exit(1)

    workers = jobs if jobs > 0 else _auto_jobs()

    def compute(path: Path) -> tuple[Path, str | None, str]:
        try:
            return path, digest(path, algo.value), ""
        except OSError as exc:
            return path, None, str(exc)

    if workers <= 1 or len(files) == 1:
        results = [compute(path) for path in files]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(compute, files))

    failed = 0
    for path, value, error in results:
        if value is None:
            typer.secho(
                f"错误：{display_path(path)} 读取失败：{error}", fg="red", err=True
            )
            failed += 1
        else:
            typer.echo(f"{value}  {display_path(path)}")

    if failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """把 hash 命令挂到主程序上。"""
    app.command(
        "hash", help="计算文件校验和（md5/sha1/sha256/sha512/blake2），或按清单校验"
    )(hash_cmd)
