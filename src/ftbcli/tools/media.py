"""ftb media —— 音频、视频格式转换二合一（基于 ffmpeg）。

音频和视频用同一个命令：目标格式是音频就抽音轨，是视频就转码；源是视频转到
音频格式时自动丢掉画面，源是音频转到视频容器时按需补上。

    ftb media 输入.mp4 -f mp3          # 视频 → 音频（抽音轨）
    ftb media 输入.flac -f mp4         # 音频 → 视频容器
    ftb media 输入.mov -f mkv -o 输出.mkv   # 指定输出文件
    ftb media 输入.mkv -o 目录/ -f mp4      # 指定输出目录（多个输入时必须是目录）
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

#: 目标扩展名 → 音频编码器
AUDIO_CODECS: dict[str, str] = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "aac": "aac",
    "flac": "flac",
    "wav": "pcm_s16le",
    "opus": "libopus",
    "ogg": "libvorbis",
    "wma": "wmav2",
    "ac3": "ac3",
    "mka": "flac",
}

#: 无损格式不需要指定码率
LOSSLESS_AUDIO = {"flac", "wav", "mka", "alac"}

#: 目标扩展名 → (视频编码器, 音频编码器)
VIDEO_CODECS: dict[str, tuple[str, str]] = {
    "mp4": ("libx264", "aac"),
    "m4v": ("libx264", "aac"),
    "mkv": ("libx264", "aac"),
    "mov": ("libx264", "aac"),
    "avi": ("mpeg4", "libmp3lame"),
    "webm": ("libvpx-vp9", "libopus"),
    "ts": ("libx264", "aac"),
    "flv": ("libx264", "aac"),
    "wmv": ("wmv2", "wmav2"),
}

AUDIO_BITRATE = {"high": "320k", "medium": "192k", "low": "128k"}
#: 支持 -crf 的视频编码器；其余按码率
CRF_CODECS = ("libx264", "libx265", "libvpx-vp9")
VIDEO_BITRATE = {"high": "6M", "medium": "3M", "low": "1500k"}
VIDEO_CRF = {"high": "18", "medium": "23", "low": "28"}
VIDEO_AUDIO_BITRATE = {"high": "256k", "medium": "192k", "low": "128k"}
GIF_WIDTH = {"high": "960", "medium": "640", "low": "480"}

QUALITIES = ("high", "medium", "low")


def target_extension(output: Path | None, fmt: str | None) -> str:
    """推断目标扩展名（不带点）。"""
    if fmt:
        return fmt.lower().lstrip(".")
    if output is not None and output.suffix:
        return output.suffix.lower().lstrip(".")
    raise ValueError("请用 -f 指定目标格式，或用带扩展名的 -o 指定输出文件")


def ffmpeg_options(ext: str, quality: str, extra: list[str]) -> list[str]:
    """拼出目标格式对应的 ffmpeg 参数。"""
    if ext in AUDIO_CODECS:
        options = ["-vn", "-c:a", AUDIO_CODECS[ext]]
        if ext not in LOSSLESS_AUDIO:
            options += ["-b:a", AUDIO_BITRATE[quality]]
    elif ext == "gif":
        options = ["-an", "-vf", f"fps=15,scale={GIF_WIDTH[quality]}:-2:flags=lanczos"]
    elif ext in VIDEO_CODECS:
        video_codec, audio_codec = VIDEO_CODECS[ext]
        options = ["-c:v", video_codec]
        if video_codec in CRF_CODECS:
            options += ["-crf", VIDEO_CRF[quality]]
        else:
            options += ["-b:v", VIDEO_BITRATE[quality]]
        if video_codec.startswith("libx26"):
            options += ["-preset", "medium"]
        options += ["-c:a", audio_codec, "-b:a", VIDEO_AUDIO_BITRATE[quality]]
    else:
        supported = sorted(set(AUDIO_CODECS) | set(VIDEO_CODECS) | {"gif"})
        raise ValueError(f"不支持的目标格式 {ext}；支持：{' '.join(supported)}")
    return options + extra


def build_command(ffmpeg: str, src: Path, dst: Path, options: list[str]) -> list[str]:
    """拼出完整 ffmpeg 命令。"""
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "warning",
        "-stats",
        "-i",
        str(src),
        *options,
        str(dst),
    ]


def media(
    sources: Annotated[
        list[Path],
        typer.Argument(
            help="要转换的文件（可多个）；给两个且第二个不存在时，第二个当作输出文件"
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="单文件时是输出文件，多个文件时是输出目录"),
    ] = None,
    fmt: Annotated[
        str | None,
        typer.Option(
            "--format",
            "--to",
            "-f",
            help="目标格式，如 mp3 / m4a / flac / mp4 / mkv / webm / gif",
        ),
    ] = None,
    quality: Annotated[
        str, typer.Option("--quality", "-q", help="质量档：high / medium / low")
    ] = "medium",
    overwrite: Annotated[
        bool, typer.Option("--overwrite", "-y", help="覆盖已存在的输出")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="只打印命令，不执行")
    ] = False,
    extra: Annotated[
        str | None,
        typer.Option("--extra", help="额外的 ffmpeg 参数，如 '-vf crop=640:480'"),
    ] = None,
) -> None:
    """音频 / 视频格式转换（二合一）；目标格式（-f/--to）决定是转音频还是转视频。"""
    inputs, positional = split_source_output(sources, output)
    output = positional
    require_files(inputs)

    if quality not in QUALITIES:
        typer.secho(f"错误：质量只能是 {' / '.join(QUALITIES)}", fg="red", err=True)
        raise typer.Exit(2)

    try:
        extension = target_extension(output, fmt)
        extra_args = shlex.split(extra) if extra else []
        options = ffmpeg_options(extension, quality, extra_args)
    except ValueError as exc:
        typer.secho(f"错误：{exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    ffmpeg = find_executable("ffmpeg")
    if ffmpeg is None and not dry_run:
        typer.secho(
            "错误：未找到 ffmpeg，请先安装（Arch：sudo pacman -S ffmpeg）",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)

    if len(inputs) > 1 and output is not None and output.suffix and output.is_file():
        typer.secho(
            f"错误：多个输入时 -o 必须是目录，而不是 {display_path(output)}",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    jobs: list[tuple[Path, Path]] = []
    for source in inputs:
        if output is None:
            target = source.with_suffix("." + extension)
        elif len(inputs) > 1 or output.is_dir():
            target = output / f"{source.stem}.{extension}"
        else:
            target = output
        if target.resolve() == source.resolve():
            typer.secho(
                f"错误：输出与输入是同一个文件，请用 -o 指定其它位置：{display_path(target)}",
                fg="red",
                err=True,
            )
            raise typer.Exit(2)
        jobs.append((source, target))

    kind = "音频" if extension in AUDIO_CODECS else "视频"
    done = failed = 0
    for source, target in jobs:
        if target.exists() and not overwrite:
            typer.secho(
                f"跳过 {display_path(target)}：已存在（用 -y 覆盖）",
                fg="yellow",
                err=True,
            )
            failed += 1
            continue

        command = build_command(ffmpeg or "ffmpeg", source, target, options)
        typer.secho(
            f"{kind}转换：{display_path(source)} → {display_path(target)}",
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
        typer.secho(f"\n共 {len(jobs)} 个任务（仅预览）", fg="bright_black")
        return
    typer.secho(
        f"\n完成 {done} 个，失败/跳过 {failed} 个",
        fg="red" if failed else "green",
    )
    if failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """把 media 命令挂到主程序上。"""
    app.command("media", help="音频 / 视频格式转换（二合一，基于 ffmpeg）")(media)
