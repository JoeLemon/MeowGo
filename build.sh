#!/usr/bin/env bash
# =============================================================================
# MeowGo 一键打包入口（macOS / Linux）
#
# 本脚本是跨平台打包核心 build.py 的 Unix 入口：
# 自动寻找 Python 3.9+，然后把全部参数原样转交给 build.py。
# Windows 请改用同目录的 build.ps1（PowerShell）。
#
# 用法：
#   ./build.sh                              # 默认打包（单文件夹）
#   ./build.sh --onefile --verify           # 单文件 + 打包后冒烟自检
#   ./build.sh --clean --no-venv            # 清场重打，用当前环境
#   bash build.sh --mirror https://pypi.tuna.tsinghua.edu.cn/simple
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
BUILD_PY="$SCRIPT_DIR/build.py"
MIN_MAJOR=3
MIN_MINOR=9

step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '    [OK] %s\n' "$*"; }
note() { printf '    [提示] %s\n' "$*"; }
die()  { printf '\n[失败] %s\n' "$*" >&2; exit 1; }

step "MeowGo 跨平台打包 [$(uname -s) / $(uname -m)]"

[ -f "$BUILD_PY" ] || die "找不到 build.py：$BUILD_PY
请确认 build.sh 与 build.py 位于同一目录。"

PY=""
for cand in python3 python; do
    command -v "$cand" >/dev/null 2>&1 || continue
    ver="$("$cand" --version 2>&1 || true)"
    if [[ "$ver" =~ ^Python\ ([0-9]+)\.([0-9]+) ]]; then
        major="${BASH_REMATCH[1]}"
        minor="${BASH_REMATCH[2]}"
        if [ "$major" -gt "$MIN_MAJOR" ] || { [ "$major" -eq "$MIN_MAJOR" ] && [ "$minor" -ge "$MIN_MINOR" ]; }; then
            PY="$cand"
            break
        fi
        note "发现 $cand（Python $major.$minor），低于 ${MIN_MAJOR}.${MIN_MINOR}，已跳过"
    fi
done

[ -n "$PY" ] || die "未找到 Python ${MIN_MAJOR}.${MIN_MINOR}+ 解释器。
macOS：brew install python（或安装 python.org 官方包）
Debian/Ubuntu：sudo apt install python3 python3-venv python3-tk"

ver="$("$PY" --version 2>&1)"
ok "使用解释器: $PY（$ver）"

# 转发全部参数，退出码原样带回
exec "$PY" "$BUILD_PY" "$@"
