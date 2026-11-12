# -*- coding: utf-8 -*-
"""每日 Git 提交日报：拉取最新代码 → 查询当天提交 → 编号输出。

给 MeowGo 的「执行脚本」步骤使用，脚本的标准输出会被存入变量，
再由「系统通知」步骤写进通知正文。也可以直接在命令行运行：

    python tools/git_daily_report.py [仓库路径]

默认仓库是 D:\\Project\\rishIsland\\gasoline-dispatch\\gd-backend-back。
拉取使用 ``git pull --ff-only --autostash``：
- 有本地未提交改动时先自动暂存，拉取后再恢复；
- 只允许快进合并，避免无人值守时产生合并提交或冲突留在半路；
- 拉取失败不阻断日报，会先把失败原因写进输出。
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys

# 默认统计的仓库（可在步骤参数里传别的路径覆盖）
DEFAULT_REPO = r"D:\Project\rishIsland\gasoline-dispatch\gd-backend-back"
# 通知正文最多列出的提交条数（太长会被系统通知截断）
MAX_LINES = 10
GIT_TIMEOUT = 120


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    """执行一条 git 命令，统一按 UTF-8 解码（git 输出通常是 UTF-8）。"""
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT)


def _first_line(text: str) -> str:
    """取错误输出的第一行，避免通知正文被一大段提示占满。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[0][:120] if lines else "未知错误"


def collect(repo: str, today: datetime.date) -> str:
    """拉取代码并返回当天的提交清单（多行文本）。"""
    if not os.path.isdir(os.path.join(repo, ".git")):
        return f"仓库不存在或不是 Git 仓库：{repo}"

    # 1) 拉取最新代码；失败也继续统计本地已有的提交
    pull_error = ""
    try:
        pull = _git(repo, "pull", "--ff-only", "--autostash")
        if pull.returncode != 0:
            pull_error = _first_line(pull.stderr or pull.stdout)
    except (OSError, subprocess.SubprocessError) as exc:
        pull_error = f"执行 git pull 失败：{exc}"

    # 2) 查询当天提交（00:00:00 – 23:59:59）
    day = today.isoformat()
    try:
        log = _git(repo, "log",
                   "--since", f"{day} 00:00:00",
                   "--until", f"{day} 23:59:59",
                   "--pretty=format:%h|%an|%ad|%s",
                   "--date=format:%H:%M")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"查询提交失败：{exc}"
    if log.returncode != 0:
        return f"查询提交失败：{_first_line(log.stderr or log.stdout)}"

    rows = [line for line in log.stdout.splitlines() if line.strip()]
    lines: list[str] = []
    if pull_error:
        lines.append(f"⚠ 拉取失败：{pull_error}")
    if not rows:
        lines.append(f"{day} 暂无提交")
        return "\n".join(lines)

    lines.append(f"{day} 共 {len(rows)} 次提交：")
    for index, row in enumerate(rows[:MAX_LINES], start=1):
        commit, author, moment, subject = (row.split("|", 3) + ["", "", ""])[:4]
        lines.append(f"{index}. {commit} {moment} {author}：{subject}")
    if len(rows) > MAX_LINES:
        lines.append(f"…… 另有 {len(rows) - MAX_LINES} 条（共 {len(rows)} 条）")
    return "\n".join(lines)


def main() -> int:
    repo = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO).strip()
    repo = os.path.expanduser(repo)
    text = collect(repo, datetime.date.today())
    print(text)
    # 仓库不存在属于配置错误，返回非 0 让这一步明确失败
    return 0 if os.path.isdir(os.path.join(repo, ".git")) else 1


if __name__ == "__main__":
    sys.exit(main())
