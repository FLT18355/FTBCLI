# v{{版本号}}

**发布日期**：{{YYYY-MM-DD}}

## 本次更新

- {{变更要点}}

## 安装

```bash
# 从 wheel 安装（推荐）
uv tool install ftbcli-{{版本号}}-py3-none-any.whl

# 或从源码安装
git clone https://github.com/FLT18355/FTBCLI
cd FTBCLI && uv tool install .
```

## 附件校验

```bash
sha256sum -c <<'EOF'
{{wheel 的 sha256}}  ftbcli-{{版本号}}-py3-none-any.whl
{{sdist 的 sha256}}  ftbcli-{{版本号}}.tar.gz
EOF
```

## 附件说明

| 附件 | 说明 |
| --- | --- |
| `ftbcli-{{版本号}}-py3-none-any.whl` | wheel 包，`uv tool install` 直接可用 |
| `ftbcli-{{版本号}}.tar.gz` | 源码包（sdist，`uv build` 生成） |
| Source code (zip / tar.gz) | GitHub 自动生成的仓库快照 |

## 环境要求

- Python >= 3.14
- 解压 `.7z` `.rar` `.cab` 等格式需要系统装有 7z 或 bsdtar（tar 系列、zip、单文件流压缩用标准库，无需外部程序）

## 生成方式

```bash
uv build --out-dir dist          # 产出 sdist + wheel
ftb hash dist/*                  # 得到校验值（也可用 sha256sum）
gh release create v{{版本号}} dist/*.tar.gz dist/*.whl \
  --title "v{{版本号}}" --notes-file {{本次说明}}.md
```