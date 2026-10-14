"""ftb unpack —— 解压常见压缩包。

按后缀自动识别格式，后缀不可信时回退到魔数嗅探：

* tar 系列： ``.tar`` ``.tar.gz/.tgz`` ``.tar.bz2/.tbz2/.tbz`` ``.tar.xz/.txz``
  ``.tar.lzma`` ``.tar.zst/.tzst``
* zip 系列： ``.zip`` 以及 ``.jar .war .whl .apk .epub`` 与 Office/ODF 文档
* 单文件流压缩： ``.gz .bz2 .xz .lzma .zst``
* 交给外部程序（7z / bsdtar）： ``.7z .rar .cab .iso .deb .rpm .cpio .ar .lzh .arj .lz4 .lz .Z``
  ``.tar.lz4 .tar.lz``
"""

from __future__ import annotations

# 重模块（tarfile/zipfile/压缩算法）一律延迟到真正解压时再导入，
# 否则每次 ftb 启动都要白白付掉几百毫秒。
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

# ---------------------------------------------------------------- 格式识别


class Kind(Enum):
    """压缩包类别，决定走哪套解压后端。"""

    TAR = "tar"
    ZIP = "zip"
    STREAM = "stream"
    SEVEN = "7z"
    RAR = "rar"
    OTHER = "other"


@dataclass(frozen=True)
class Format:
    """识别结果。"""

    kind: Kind
    #: tar 的 tarfile 打开模式；stream 的压缩算法名
    param: str = ""

    @property
    def is_dir_archive(self) -> bool:
        """是否为“多文件目录型”压缩包（tar/zip/7z/rar/其它）。"""
        return self.kind is not Kind.STREAM


TAR_MODES = {
    ".tar": "r:",
    ".tar.gz": "r:gz",
    ".tgz": "r:gz",
    ".tar.bz2": "r:bz2",
    ".tbz2": "r:bz2",
    ".tbz": "r:bz2",
    ".tar.xz": "r:xz",
    ".txz": "r:xz",
    ".tar.lzma": "r:xz",
    ".tar.zst": "r:zst",
    ".tzst": "r:zst",
}

ZIP_SUFFIXES = (
    ".zip",
    ".jar",
    ".war",
    ".whl",
    ".apk",
    ".epub",
    ".xpi",
    ".cbz",
    ".docx",
    ".xlsx",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
)

STREAM_SUFFIXES = {
    ".gz": "gz",
    ".bz2": "bz2",
    ".xz": "xz",
    ".lzma": "lzma",
    ".zst": "zst",
}

# 需要外部程序的后缀：外部后端依次尝试 7z、bsdtar
EXTERNAL_SUFFIXES = {
    ".7z": Kind.SEVEN,
    ".rar": Kind.RAR,
    ".cab": Kind.OTHER,
    ".iso": Kind.OTHER,
    ".deb": Kind.OTHER,
    ".rpm": Kind.OTHER,
    ".cpio": Kind.OTHER,
    ".ar": Kind.OTHER,
    ".lzh": Kind.OTHER,
    ".lha": Kind.OTHER,
    ".arj": Kind.OTHER,
    ".lz4": Kind.OTHER,
    ".lz": Kind.OTHER,
    ".z": Kind.OTHER,
    ".tar.lz4": Kind.TAR,
    ".tar.lz": Kind.TAR,
    ".tar.z": Kind.TAR,
}

_SUFFIX_TABLE: dict[str, Format] = {}
_SUFFIX_TABLE.update({s: Format(Kind.TAR, m) for s, m in TAR_MODES.items()})
_SUFFIX_TABLE.update({s: Format(Kind.ZIP) for s in ZIP_SUFFIXES})
_SUFFIX_TABLE.update({s: Format(Kind.STREAM, c) for s, c in STREAM_SUFFIXES.items()})
_SUFFIX_TABLE.update({s: Format(k) for s, k in EXTERNAL_SUFFIXES.items()})

#: 按长度倒序，保证 ``.tar.gz`` 先于 ``.gz`` 命中
_SORTED_SUFFIXES = sorted(_SUFFIX_TABLE, key=len, reverse=True)

#: 魔数嗅探表（无后缀或后缀不认识时使用）
_MAGIC_TABLE: tuple[tuple[bytes, Format], ...] = (
    (b"\x1f\x8b", Format(Kind.STREAM, "gz")),
    (b"BZh", Format(Kind.STREAM, "bz2")),
    (b"\xfd7zXZ\x00", Format(Kind.STREAM, "xz")),
    (b"\x28\xb5\x2f\xfd", Format(Kind.STREAM, "zst")),
    (b"7z\xbc\xaf\x27\x1c", Format(Kind.SEVEN)),
    (b"Rar!\x1a\x07", Format(Kind.RAR)),
    (b"PK\x03\x04", Format(Kind.ZIP)),
    (b"PK\x05\x06", Format(Kind.ZIP)),
    (b"PK\x07\x08", Format(Kind.ZIP)),
    (b"MSCF", Format(Kind.OTHER)),
    (b"!<arch>\n", Format(Kind.OTHER)),
    (b"\xed\xab\xee\xdb", Format(Kind.OTHER)),
)


class UnsupportedFormat(Exception):
    """无法识别的格式。"""


def _sniff(path: Path) -> Format | None:
    """读文件头猜测格式。"""
    with path.open("rb") as fh:
        head = fh.read(512 * 1024)
    for magic, fmt in _MAGIC_TABLE:
        if head.startswith(magic):
            return fmt
    # tar：``ustar`` 位于偏移 257；ISO9660 的 ``CD001`` 位于偏移 32769
    if len(head) >= 265 and head[257:262] == b"ustar":
        return Format(Kind.TAR, "r:")
    if len(head) >= 32774 and head[32769:32774] == b"CD001":
        return Format(Kind.OTHER)
    return None


def detect_format(path: Path) -> Format:
    """按后缀识别格式，失败则嗅探魔数。"""
    name = path.name.lower()
    for suffix in _SORTED_SUFFIXES:
        if name.endswith(suffix):
            return _SUFFIX_TABLE[suffix]
    fmt = _sniff(path)
    if fmt is None:
        raise UnsupportedFormat(f"无法识别压缩格式：{path.name}")
    return fmt


def strip_suffix(path: Path) -> str:
    """去掉已知压缩后缀，得到解压目录/文件名。"""
    name = path.name
    low = name.lower()
    for suffix in _SORTED_SUFFIXES:
        if low.endswith(suffix):
            return name[: len(name) - len(suffix)] or "unpacked"
    return name


# ---------------------------------------------------------------- 外部程序

_SEVENZIP_CANDIDATES = ("7zz", "7z", "7za", "7zr")


def find_7z() -> str | None:
    """查找可用的 7-Zip 可执行文件。"""
    for name in _SEVENZIP_CANDIDATES:
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def find_bsdtar() -> str | None:
    """查找 bsdtar（libarchive，格式覆盖面广）。"""
    exe = shutil.which("bsdtar")
    if exe:
        return exe
    tar = shutil.which("tar")
    if tar and _is_bsdtar(tar):
        return tar
    return None


def _is_bsdtar(cmd: str) -> bool:
    """判断名为 tar 的程序是否其实是 bsdtar。"""
    try:
        out = subprocess.run(
            [cmd, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except OSError:
        return False
    return "bsdtar" in (out.stdout + out.stderr).lower()


class BackendError(Exception):
    """后端自身无法处理该包（可回退到别的后端）。"""


class ExtractFailed(Exception):
    """解压失败，消息可直接展示给用户。"""


def _run_external(cmd: list[str]) -> None:
    """执行外部解压程序，失败时抛出带 stderr 的异常。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise BackendError(f"无法执行 {cmd[0]}：{exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " / ".join(detail[-3:]) if detail else f"退出码 {proc.returncode}"
        raise BackendError(f"{Path(cmd[0]).name} 失败：{tail}")


def _extract_7z(archive: Path, dest: Path, password: str | None) -> None:
    exe = find_7z()
    if exe is None:
        raise BackendError("未安装 7-Zip（7zz/7z/7za）")
    cmd = [exe, "x", "-y", "-bso0", "-bsp0", f"-o{dest}", "--", str(archive)]
    if password:
        cmd.insert(1, f"-p{password}")
    _run_external(cmd)


def _extract_bsdtar(archive: Path, dest: Path, password: str | None) -> None:
    if password:
        raise BackendError("bsdtar 不支持密码")
    exe = find_bsdtar()
    if exe is None:
        raise BackendError("未安装 bsdtar")
    _run_external([exe, "-xf", str(archive), "-C", str(dest)])


def _extract_external(
    archive: Path, dest: Path, password: str | None, created: bool
) -> None:
    """外部后端链：7z → bsdtar。

    ``created`` 为真表示目标目录是本次新建的，回退时才可以清掉半成品。
    """
    errors: list[str] = []
    for backend in (_extract_7z, _extract_bsdtar):
        try:
            backend(archive, dest, password)
        except BackendError as exc:
            errors.append(str(exc))
            if created:
                shutil.rmtree(dest, ignore_errors=True)
                dest.mkdir(parents=True, exist_ok=True)
            continue
        return
    raise ExtractFailed("外部解压程序均失败：" + "；".join(errors))


# ---------------------------------------------------------------- 内建后端


def _extract_tar(archive: Path, dest: Path, mode: str) -> int:
    """用 tarfile 解压；``data`` 过滤器拒绝越界路径与危险链接。"""
    import tarfile

    try:
        with tarfile.open(archive, mode) as tf:
            members = tf.getmembers()
            tf.extractall(dest, filter="data")
    except tarfile.FilterError as exc:
        raise ExtractFailed(f"压缩包含有不安全条目，已拒绝解压：{exc}") from exc
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise BackendError(f"tarfile 无法处理：{exc}") from exc
    return len(members)


def _zip_is_symlink(info: object) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _zip_name_unsafe(name: str) -> bool:
    """判断 zip 内部路径是否越界（绝对路径、.. 或盘符）。"""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return True
    if name.startswith(("/", "\\")) or (len(parts[0]) >= 2 and parts[0][1] == ":"):
        return True
    return any(p == ".." for p in parts)


def _extract_zip(archive: Path, dest: Path, password: str | None) -> int:
    """用 zipfile 解压，先整体校验路径，再落盘。"""
    import zipfile

    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            bad = next(
                (i.filename for i in infos if _zip_name_unsafe(i.filename)), None
            )
            if bad is not None:
                raise ExtractFailed(f"zip 内含越界路径，已拒绝解压：{bad}")
            if any(i.compress_type == 99 for i in infos):
                raise BackendError("zip 使用了 WinZip AES 加密，内建后端不支持")
            if any(_zip_is_symlink(i) for i in infos):
                raise BackendError("zip 内含符号链接，内建后端不支持")
            zf.extractall(dest, pwd=password.encode() if password else None)
    except zipfile.BadZipFile as exc:
        raise BackendError(f"不是有效的 zip 文件：{exc}") from exc
    except RuntimeError as exc:  # 缺密码 / 密码错误
        raise ExtractFailed(f"{exc}（加密包请用 -p 指定密码）") from exc
    return len(infos)


def _extract_stream(archive: Path, out: Path, comp: str) -> int:
    """单文件流压缩 → 解出一个文件。"""
    import bz2
    import gzip
    import lzma
    from compression import zstd

    openers = {
        "gz": gzip.open,
        "bz2": bz2.open,
        "xz": lzma.open,
        "lzma": lzma.open,
        "zst": zstd.open,
    }
    if out.resolve() == archive.resolve():
        raise ExtractFailed(f"解压结果与源文件同名，请用 -d 指定其它目录：{out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with openers[comp](archive, "rb") as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    except Exception as exc:
        out.unlink(missing_ok=True)
        raise ExtractFailed(f"解压失败：{exc}") from exc
    return 1


# ---------------------------------------------------------------- 命令实现


def _resolve_dest(archive: Path, dest: Path | None, fmt: Format) -> Path:
    """计算输出路径：目录型默认 <包名>/，流压缩默认当前目录。"""
    if dest is not None:
        return dest
    return Path(strip_suffix(archive)) if fmt.is_dir_archive else Path(".")


def _prepare_dir(dest: Path, force: bool) -> bool:
    """准备输出目录，非空且未加 -f 时拒绝；返回目录是否由本次调用新建。"""
    if dest.exists():
        if not dest.is_dir():
            raise ExtractFailed(f"目标已存在且不是目录：{dest}")
        if any(dest.iterdir()) and not force:
            raise ExtractFailed(f"目标目录已存在且非空：{dest}（用 -f 覆盖）")
        return False
    dest.mkdir(parents=True)
    return True


def _prepare_file(out: Path, force: bool) -> bool:
    """准备输出文件，已存在且未加 -f 时拒绝；返回文件是否由本次调用新建。"""
    if out.exists() and not force:
        raise ExtractFailed(f"目标文件已存在：{out}（用 -f 覆盖）")
    out.parent.mkdir(parents=True, exist_ok=True)
    return not out.exists()


def unpack(
    archive: Annotated[
        Path,
        typer.Argument(
            help="压缩包路径",
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ],
    dest: Annotated[
        Path | None,
        typer.Option(
            "--dest",
            "-d",
            help="解压目标目录；默认按包名新建 <包名>/，单文件流压缩默认解到当前目录",
            file_okay=False,
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="目标已存在时覆盖")
    ] = False,
    password: Annotated[
        str | None, typer.Option("--password", "-p", help="加密包的密码")
    ] = None,
) -> None:
    """解压压缩包（tar 系列、zip 系列、7z、rar 及常见单文件流压缩）。"""
    try:
        fmt = detect_format(archive)
    except UnsupportedFormat as exc:
        typer.secho(f"错误：{exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

    target = _resolve_dest(archive, dest, fmt).resolve()
    out_file = None if fmt.is_dir_archive else target / strip_suffix(archive)
    created = False
    count: int | None = None

    try:
        created = (
            _prepare_dir(target, force)
            if fmt.is_dir_archive
            else _prepare_file(out_file, force)
        )

        if fmt.kind is Kind.STREAM:
            count = _extract_stream(archive, out_file, fmt.param)
        else:
            try:
                if fmt.kind is Kind.TAR and fmt.param:
                    count = _extract_tar(archive, target, fmt.param)
                elif fmt.kind is Kind.ZIP:
                    count = _extract_zip(archive, target, password)
                else:
                    _extract_external(archive, target, password, created)
            except BackendError as exc:
                typer.secho(f"提示：{exc}，改用外部程序…", fg="yellow", err=True)
                _extract_external(archive, target, password, created)
                count = None
    except (ExtractFailed, BackendError) as exc:
        if created and fmt.is_dir_archive:
            shutil.rmtree(target, ignore_errors=True)
        typer.secho(f"错误：{exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

    where = out_file or target
    detail = f"{count} 个条目" if count is not None else "已交由外部程序解压"
    typer.secho(f"解压完成：{_display(where)}（{detail}）", fg="green")

    if fmt.kind is Kind.STREAM:
        _hint_inner(out_file)


def _display(path: Path) -> str:
    """尽量显示相对当前目录的短路径。"""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _hint_inner(path: Path) -> None:
    """流压缩解出来的还是压缩包时，提示可再解一次。"""
    try:
        detect_format(path)
    except UnsupportedFormat:
        return
    typer.secho(
        f"提示：{path.name} 本身仍是压缩包，可再执行 ftb unpack {_display(path)}",
        fg="yellow",
        err=True,
    )


def register(app: typer.Typer) -> None:
    """把 unpack 命令挂到主程序上。"""
    app.command(
        "unpack", help="解压压缩包（tar 系列 / zip 系列 / 7z / rar / 单文件流压缩）"
    )(unpack)
