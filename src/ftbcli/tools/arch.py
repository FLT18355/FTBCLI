"""ftb arch —— Arch Linux 系统优化。

逐个确认后清理各类缓存，最后做系统升级：``sudo pacman -Syyu``。

只支持 Arch Linux 及其衍生发行版（依据 ``/etc/os-release`` 判断）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

#: 原生 Arch
ARCH_IDS = {"arch", "archlinux", "archarm"}
#: 常见衍生发行版
ARCH_DERIVATIVES = {
    "endeavouros",
    "manjaro",
    "cachyos",
    "garuda",
    "artix",
    "arcolinux",
    "rebornos",
}


@dataclass(frozen=True)
class Step:
    """一个优化步骤。"""

    title: str
    detail: str
    command: str
    #: 缺失的依赖程序；非空表示本机无法执行本步
    missing: str | None = None
    #: 展示给用户的命令（省略即为 command 本身）
    shown: str = ""

    @property
    def available(self) -> bool:
        """本机是否具备执行条件。"""
        return self.missing is None

    @property
    def display(self) -> str:
        """展示用的命令文本。"""
        return self.shown or self.command


def _which(*names: str) -> str | None:
    """返回第一个存在的可执行文件名。"""
    for name in names:
        if shutil.which(name):
            return name
    return None


def build_steps() -> list[Step]:
    """按顺序组装优化步骤；缺依赖的步骤保留在列表里但标记为不可用。"""
    steps: list[Step] = []

    def add(
        title: str, detail: str, command: str, dep: str | None, shown: str = ""
    ) -> None:
        """登记一步；dep 为需要的程序（None 表示必然可用）。"""
        missing = None if dep is None or shutil.which(dep) else dep
        steps.append(Step(title, detail, command, missing, shown))

    # uv run 会让 uv 自己持有缓存锁，此时清理会一直等待，需 --force 打破
    in_uv_run = bool(os.environ.get("UV_RUN_RECURSION_DEPTH"))
    add(
        "uv 缓存",
        "清理 uv 的下载缓存与构建产物"
        + (
            "（当前由 uv run 启动，需 --force 打破 uv 自身的缓存锁）"
            if in_uv_run
            else ""
        ),
        "uv cache clean --force" if in_uv_run else "uv cache clean",
        "uv",
    )
    add(
        "bun 缓存",
        "清空 bun 的全局包缓存",
        "bun pm cache rm",
        "bun",
    )
    add(
        "npm 缓存",
        "清空 npm 的 _cacache 缓存",
        "npm cache clean --force",
        "npm",
    )
    add(
        "pnpm 仓库",
        "移除 pnpm store 中不再被引用的包",
        "pnpm store prune",
        "pnpm",
    )
    add(
        "yarn 缓存",
        "清空 yarn 的包缓存",
        "yarn cache clean",
        "yarn",
    )
    pip = _which("pip", "pip3")
    add(
        "pip 缓存",
        "清空 pip 的 wheel 与 HTTP 缓存",
        f"{pip} cache purge" if pip else "pip cache purge",
        pip or "pip",
    )
    if shutil.which("cargo-cache"):
        add(
            "cargo 下载缓存",
            "用 cargo-cache 自动清理不再需要的 crate",
            "cargo-cache --autoclean",
            "cargo",
        )
    else:
        add(
            "cargo 下载缓存",
            "删除 ~/.cargo/registry/cache 里的 .crate 文件（下次构建会重新下载）",
            'rm -rf "$HOME"/.cargo/registry/cache/*',
            "cargo",
        )
    add(
        "go 构建缓存",
        "清空 go 的编译缓存",
        "go clean -cache",
        "go",
    )
    add(
        "docker 无用数据",
        "删除已停止的容器、悬空镜像与未使用的网络",
        "docker system prune -f",
        "docker",
    )
    if shutil.which("paccache"):
        add(
            "pacman 包缓存",
            "用 paccache 保留最近 1 个版本的包",
            "sudo paccache -rk1",
            "pacman",
        )
    else:
        add(
            "pacman 包缓存",
            "删除 pacman 缓存里已卸载的包（已安装的包缓存保留）",
            "sudo pacman -Sc --noconfirm",
            "pacman",
        )
    helper = _which("yay", "paru")
    add(
        "AUR 构建缓存",
        "清理 AUR 助手下载的源码与构建产物",
        f"{helper} -Sc --noconfirm" if helper else "yay -Sc --noconfirm",
        helper or "yay",
    )
    add(
        "systemd 日志",
        "日志只保留最近 200M",
        "sudo journalctl --vacuum-size=200M",
        "journalctl",
    )
    add(
        "孤儿包",
        "删除没有任何包依赖的孤立包（没装孤儿包时自动跳过）",
        'orphans=$(pacman -Qtdq) && { [ -z "$orphans" ] && echo '
        '"没有孤儿包" || sudo pacman -Rns --noconfirm $orphans; }',
        "pacman",
        shown="sudo pacman -Rns $(pacman -Qtdq)（无孤儿包时自动跳过）",
    )
    add(
        "系统升级",
        "同步数据库并升级全部软件包",
        "sudo pacman -Syyu",
        "pacman",
    )
    return steps


def _os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    """解析 os-release。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def is_arch(path: Path = Path("/etc/os-release")) -> bool:
    """判断系统是否为 Arch Linux 系。"""
    fields = _os_release(path)
    if not fields:
        return shutil.which("pacman") is not None
    ids = {fields.get("ID", "")} | set(fields.get("ID_LIKE", "").split())
    ids.discard("")
    return bool(ids & (ARCH_IDS | ARCH_DERIVATIVES))


def _confirm(prompt: str) -> bool:
    """向用户确认；无终端时直接判定为否。"""
    try:
        return typer.confirm(prompt, default=False)
    except EOFError, typer.Abort:
        return False


def arch(
    list_only: Annotated[
        bool, typer.Option("--list", "-l", help="只列出步骤，不执行")
    ] = False,
) -> None:
    """Arch Linux 系统优化：逐个确认后清理各类缓存，最后升级系统。"""
    if not is_arch():
        typer.secho("错误：本命令仅支持 Arch Linux 及其衍生发行版", fg="red", err=True)
        raise typer.Exit(1)

    steps = build_steps()

    if list_only:
        typer.secho(f"共 {len(steps)} 个步骤（未安装的程序会自动跳过）：", fg="cyan")
        for index, step in enumerate(steps, 1):
            state = "" if step.available else f"    ← 未安装 {step.missing}"
            typer.echo(f"{index:>2}. {step.title}{state}")
            typer.echo(f"    {step.detail}")
            typer.echo(f"    $ {step.display}")
        return

    done = skipped = failed = 0
    for index, step in enumerate(steps, 1):
        total = f"[{index}/{len(steps)}]"

        if not step.available:
            typer.secho(
                f"{total} 跳过 {step.title}：未安装 {step.missing}", fg="yellow"
            )
            skipped += 1
            continue

        typer.secho(f"{total} {step.title}", fg="cyan", bold=True)
        typer.echo(f"    {step.detail}")
        typer.secho(f"    $ {step.display}", fg="bright_black")

        if not _confirm("    执行这一步？"):
            typer.secho("    已跳过", fg="yellow")
            skipped += 1
            continue

        result = subprocess.run(step.command, shell=True, check=False)
        if result.returncode == 0:
            typer.secho("    完成", fg="green")
            done += 1
        else:
            typer.secho(f"    失败（退出码 {result.returncode}）", fg="red")
            failed += 1

    typer.secho(
        f"\n汇总：执行 {done} 步，跳过 {skipped} 步，失败 {failed} 步",
        fg="red" if failed else "green",
    )
    if failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """把 arch 命令挂到主程序上。"""
    app.command("arch", help="Arch Linux 系统优化（缓存清理 + 系统升级，逐步确认）")(
        arch
    )
