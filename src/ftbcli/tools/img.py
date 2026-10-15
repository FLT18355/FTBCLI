"""ftb img —— 图片格式转换与缩放（基于 ffmpeg）。

ftb img 输入.png -f jpg            # 换格式
ftb img 输入.jpg -w 1280           # 等比缩到宽 1280
ftb img 输入.heic -f webp --max 1600   # 长边不超过 1600
ftb img *.png -o 输出目录/ -f webp -q high
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from ftbcli.util import (
    display_path,
    find_executable,
    require_files,
    split_source_output,
)

#: 各格式的质量参数（high / medium / low）
QUALITY_ARGS: dict[str, dict[str, list[str]]] = {
    "jpg": {"high": ["-q:v", "2"], "medium": ["-q:v", "5"], "low": ["-q:v", "9"]},
    "jpeg": {"high": ["-q:v", "2"], "medium": ["-q:v", "5"], "low": ["-q:v", "9"]},
    "webp": {
        "high": ["-quality", "90"],
        "medium": ["-quality", "80"],
        "low": ["-quality", "70"],
    },
    "avif": {"high": ["-crf", "18"], "medium": ["-crf", "23"], "low": ["-crf", "30"]},
    "png": {
        "high": ["-compression_level", "3"],
        "medium": ["-compression_level", "6"],
        "low": ["-compression_level", "9"],
    },
}

#: 支持的图片格式
IMAGE_FORMATS = (
    "png",
    "jpg",
    "jpeg",
    "webp",
    "avif",
    "bmp",
    "tiff",
    "tga",
    "qoi",
    "ico",
)

#: 不支持透明通道的目标格式，转码前先转成 rgb24
NO_ALPHA = {"jpg", "jpeg", "bmp"}

QUALITIES = ("high", "medium", "low")


def probe_size(ffprobe: str, path: Path) -> tuple[int, int] | None:
    """读取图片的宽高；失败返回 None。"""
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    parts = result.stdout.strip().split(",")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def scale_filter(
    width: int | None,
    height: int | None,
    maximum: int | None,
    size: tuple[int, int] | None,
) -> str | None:
    """按选项拼出 scale 滤镜。"""
    if maximum is not None:
        if size is None:
            raise ValueError("无法读取图片尺寸，--max 不可用；可改用 -w 或 -h")
        longest = max(size)
        if longest <= maximum:
            return None
        edge = maximum - maximum % 2
        return f"scale={edge}:{edge}:force_original_aspect_ratio=decrease"
    if width and height:
        return f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    if width:
        return f"scale={width}:-2"
    if height:
        return f"scale=-2:{height}"
    return None


def build_command(
    ffmpeg: str,
    src: Path,
    dst: Path,
    filters: list[str],
    quality_args: list[str],
    extra: list[str],
    output_args: list[str],
) -> list[str]:
    """拼出完整 ffmpeg 命令。"""
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "warning",
        "-i",
        str(src),
    ]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += ["-frames:v", "1"]
    command += quality_args
    command += output_args
    command += extra
    command.append(str(dst))
    return command


def img(
    sources: Annotated[
        list[Path],
        typer.Argument(
            help="要转换的图片（可多个）；给两个且第二个不存在时，第二个当作输出文件"
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="单张时是输出文件，多张时是输出目录"),
    ] = None,
    fmt: Annotated[
        str | None,
        typer.Option(
            "--format",
            "--to",
            "-f",
            help="目标格式：png / jpg / webp / avif / bmp / tiff 等",
        ),
    ] = None,
    quality: Annotated[
        str, typer.Option("--quality", "-q", help="质量档：high / medium / low")
    ] = "medium",
    width: Annotated[
        int | None, typer.Option("--width", "-w", help="等比缩放到指定宽度")
    ] = None,
    height: Annotated[
        int | None, typer.Option("--height", "-H", help="等比缩放到指定高度")
    ] = None,
    maximum: Annotated[
        int | None,
        typer.Option("--max", "-m", help="长边上限，只缩不放（与 -w / -H 互斥）"),
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", "-y", help="覆盖已存在的输出")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="只打印命令，不执行")
    ] = False,
    extra: Annotated[
        str | None,
        typer.Option("--extra", help="额外的 ffmpeg 参数，如 '-vf crop=100:100'"),
    ] = None,
) -> None:
    """图片格式转换与等比缩放。"""
    inputs, positional = split_source_output(sources, output)
    output = positional
    require_files(inputs)

    if quality not in QUALITIES:
        typer.secho(f"错误：质量只能是 {' / '.join(QUALITIES)}", fg="red", err=True)
        raise typer.Exit(2)
    if maximum is not None and (width or height):
        typer.secho("错误：--max 与 -w / -H 只能选一个", fg="red", err=True)
        raise typer.Exit(2)

    extension = (
        (fmt or (output.suffix if output and output.suffix else "")).lower().lstrip(".")
    )
    if not extension:
        typer.secho(
            "错误：请用 -f 指定目标格式，或用带扩展名的 -o 指定输出文件",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    if extension not in IMAGE_FORMATS:
        typer.secho(
            f"错误：不支持的目标格式 {extension}；支持：{' '.join(IMAGE_FORMATS)}",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    ffmpeg = find_executable("ffmpeg")
    if ffmpeg is None and not dry_run:
        typer.secho(
            "错误：未找到 ffmpeg，请先安装（Arch：sudo pacman -S ffmpeg）",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)
    ffprobe = find_executable("ffprobe")

    if len(inputs) > 1 and output is not None and output.suffix and output.is_file():
        typer.secho(
            f"错误：多张图片时 -o 必须是目录，而不是 {display_path(output)}",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    extra_args = shlex.split(extra) if extra else []
    quality_args = QUALITY_ARGS.get(extension, {}).get(quality, [])
    # jpg 用 420 采样，兼容性最好
    output_args = ["-pix_fmt", "yuvj420p"] if extension in {"jpg", "jpeg"} else []

    done = failed = 0
    for source in inputs:
        if output is None:
            target = source.with_suffix("." + extension)
        elif len(inputs) > 1 or output.is_dir():
            target = output / f"{source.stem}.{extension}"
        else:
            target = output
        if target.resolve() == source.resolve():
            typer.secho(
                f"跳过 {display_path(source)}：输出与输入相同，请用 -o 指定其它位置",
                fg="red",
                err=True,
            )
            failed += 1
            continue
        if target.exists() and not overwrite:
            typer.secho(
                f"跳过 {display_path(target)}：已存在（用 -y 覆盖）",
                fg="yellow",
                err=True,
            )
            failed += 1
            continue

        need_size = maximum is not None
        size = probe_size(ffprobe, source) if need_size and ffprobe else None
        try:
            scale = scale_filter(width, height, maximum, size)
        except ValueError as exc:
            typer.secho(f"错误：{display_path(source)} {exc}", fg="red", err=True)
            raise typer.Exit(2) from exc

        filters: list[str] = []
        if scale:
            filters.append(scale)
        if extension in NO_ALPHA:
            filters.append("format=rgb24")

        command = build_command(
            ffmpeg or "ffmpeg",
            source,
            target,
            filters,
            quality_args,
            extra_args,
            output_args,
        )
        detail = f"，{scale.split(':')[0].removeprefix('scale=')}" if scale else ""
        typer.secho(
            f"图片转换：{display_path(source)} → {display_path(target)}{detail}",
            fg="cyan",
            bold=True,
        )
        if dry_run:
            typer.secho(
                "  " + " ".join(shlex.quote(part) for part in command),
                fg="bright_black",
            )
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(command, check=False)
        if result.returncode == 0:
            done += 1
        else:
            typer.secho(
                f"  转换失败（ffmpeg 退出码 {result.returncode}）", fg="red", err=True
            )
            failed += 1
            target.unlink(missing_ok=True)

    if dry_run:
        typer.secho(f"\n共 {len(inputs)} 个任务（仅预览）", fg="bright_black")
        return
    typer.secho(
        f"\n完成 {done} 个，失败/跳过 {failed} 个", fg="red" if failed else "green"
    )
    if failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """把 img 命令挂到主程序上。"""
    app.command("img", help="图片格式转换与等比缩放（基于 ffmpeg）")(img)
