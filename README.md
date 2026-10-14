# FTBCLI —— FLT18355 的工具箱

用 [Typer](https://typer.tiangolo.com/) 写的个人命令行工具箱，入口命令 `ftb`。

```bash
uv sync && uv run ftb --help    # 开发运行
uv tool install .               # 或装成全局命令 ftb
```

子命令按需加载（只 import 用到的那个工具），启动开销主要来自 typer/click 本身（约 0.6 秒）。

## 命令

### `ftb unpack 压缩包`

解压压缩包，按后缀识别格式（`.tar.gz` 先于 `.gz`；后缀不可信时按文件头嗅探）。

- 选项：`-d/--dest 目标目录`、`-f/--force 覆盖`、`-p/--password 密码`
- 默认解到 `<包名>/`，单文件流压缩解到当前目录
- 支持 tar 系列（`.tar` `.tar.gz/.tgz` `.tar.bz2/.tbz2` `.tar.xz/.txz` `.tar.zst/.tzst`）、zip 系列（`.zip` `.jar` `.whl` 及 Office/ODF 文档）、单文件流（`.gz` `.bz2` `.xz` `.lzma` `.zst`）、以及需外部程序的 `.7z` `.rar` 等（用 7z / bsdtar）
- 越界条目（`..`、绝对路径、盘符）一律拒绝；非空目标目录需 `-f`

### `ftb hash 文件或目录`

计算校验和，输出格式与 `sha256sum` 一致，可直接重定向成清单文件。

- 选项：`-a/--algo`（md5/sha1/sha256/sha512/blake2b/blake2s，默认 sha256）、`-r/--recursive`、`-j/--jobs`
- `ftb hash -c 清单文件` 校验模式：逐项「通过/校验失败/缺少文件」，有失败时退出码 1
- 清单里的路径先按清单所在目录解析，找不到再按当前目录解析

### `ftb dup [目录]`

查找重复文件，按「文件大小 → 头部 64K → 全文哈希」逐级筛选，少读盘。

- 选项：`-m/--min-size`（如 `1M`）、`-j/--jobs`、`--delete`（确认后删除每组多余的副本）、`-y`
- 硬链接只算一个文件，符号链接不跟随；默认只报告不删除

### `ftb arch`

Arch Linux 专用系统优化，**每一步都先询问是否执行**：清理 uv / bun / npm / pnpm / yarn / pip / cargo / go / docker / pacman / AUR 助手缓存、systemd 日志、孤儿包，最后执行 `sudo pacman -Syyu`。未安装的程序自动跳过。

- `ftb arch --list` 只看步骤不执行
- 仅支持 Arch Linux 及衍生发行版