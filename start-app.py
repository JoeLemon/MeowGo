# -*- coding: utf-8 -*-
"""程序入口：先检查依赖，再启动界面。

无头模式（给 Windows 任务计划程序用，不弹界面）：
    python start-app.py --run 组名 [--loop] [--data 数据.csv]
                        [--from-row N] [--report 报告目录] [--list]
"""
from __future__ import annotations

import importlib
import os
import sys

from rpa import logs


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

REQUIRED = [
    ("pyautogui", "pyautogui==0.9.54"),
    ("pyperclip", "pyperclip"),
    ("PIL", "pillow"),
    ("xlrd", "xlrd"),
    ("openpyxl", "openpyxl"),
]
OPTIONAL = [("cv2", "opencv-python")]


def _missing() -> list[str]:
    missing = []
    for module, package in REQUIRED:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    return missing


def _warn_optional() -> None:
    """可选依赖缺失时给一句提醒（图片识别依赖 OpenCV）。"""
    for module, package in OPTIONAL:
        try:
            importlib.import_module(module)
        except ImportError:
            print(f"警告：未安装 {package}，图片识别步骤将无法运行。"
                  f"安装命令：pip install {package}", file=sys.stderr)


def _check_deps() -> bool:
    missing = _missing()
    if not missing:
        return True
    message = (
        "缺少以下依赖，无法启动：\n\n  " + "\n  ".join(missing) +
        "\n\n请复制下面的命令到命令行执行：\n\n"
        "pip install " + " ".join(missing) +
        "\n\n国内网络慢可加镜像：\n"
        "pip install " + " ".join(missing) +
        " -i https://pypi.tuna.tsinghua.edu.cn/simple"
    )
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少依赖", message)
        root.destroy()
    except Exception:
        print(message)
    return False


def _load_project():
    """读入状态文件里的执行组（与界面共用同一份数据）。"""
    import json

    from rpa import gui as gui_module
    from rpa.engine import Project

    settings_path = gui_module.SETTINGS_PATH
    if not os.path.exists(settings_path):
        print(f"找不到状态文件：{settings_path}")
        print("请先在界面里配好执行组并保存，再用命令行运行。")
        return None
    with open(settings_path, encoding="utf-8") as fh:
        data = json.load(fh)
    return Project.from_dict(data), data


def _load_data(path: str) -> list[dict]:
    """把 CSV / Excel 数据文件读成 [{列名: 值}, ...]（引擎同款逻辑）。"""
    from rpa.engine import load_data

    return load_data(path)


def _write_report(report: dict, directory: str, result: str) -> str:
    """把运行报告写成 markdown，并刷新 index.html。"""
    import time

    from rpa.engine import report_markdown, write_report_index

    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    path = os.path.join(directory, f"run_{stamp}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report_markdown(report, result, report_dir=directory))
    write_report_index(directory)
    return path


def _run_headless(args) -> int:
    loaded = _load_project()
    if loaded is None:
        return 1
    project, state = loaded

    from rpa.engine import Executor, validate_environment, validate_project

    if args.list:
        print("执行组：")
        for group in project.groups:
            print(f"  {group.name}（{len(group.steps)} 步）")
        return 0

    # 避免两个自动运行同时抢鼠标；锁在进程退出时自动释放
    from rpa.lock import InstanceLock, make_lock_name

    lock = InstanceLock(make_lock_name(_app_dir(), "RUN"))
    if not lock.acquire():
        print("另一个 MeowGo 正在运行，本次跳过。")
        return 0

    groups = {group.name: group.steps for group in project.groups}
    if args.run:
        if args.run not in groups:
            print(f"找不到执行组：{args.run}")
            print("可用组：" + "、".join(groups))
            lock.release()
            return 1
        entry = args.run
    else:
        entry = None
        if not groups:
            print("状态文件里还没有执行组。")
            lock.release()
            return 1

    image_dir = state.get("image_dir", "images")
    problems = (validate_environment(project.groups) +
                validate_project(project.groups, base_dir=image_dir,
                                 check_images=True))
    if problems:
        print("运行前校验发现问题：")
        for problem in problems:
            print(f"  - {problem}")
        print("仍按当前配置继续执行……")

    data = _load_data(args.data) if args.data else []
    if args.data and not data:
        print(f"警告：数据文件 {args.data} 里没有数据行")
    row_offset = max(0, int(getattr(args, "from_row", 0) or 0))
    if data and row_offset >= len(data):
        print(f"从第 {row_offset + 1} 行继续，但数据只有 {len(data)} 行。")
        lock.release()
        return 1

    report_dir = (os.path.abspath(args.report) if args.report
                  else os.path.join(_app_dir(), "reports"))
    run_log = logs.RunLog(report_dir, args.run or "全部执行组")
    # 状态文件里 delay / confidence 等设置是平铺保存的
    executor = Executor()
    executor.base_dir = image_dir
    executor.groups = groups
    executor.order = project.names()
    executor.confidence = float(state.get("confidence", 0.9))
    executor.data = data
    executor.row_offset = row_offset if data else 0
    executor.shot_dir = (os.path.join(report_dir, "shots")
                         if state.get("shot_on_fail", True) else "")

    def _log(level: str, message: str) -> None:
        print(f"[{level}] {message}", flush=True)
        run_log.write(level, message)

    executor.log = _log

    result = executor.run(entry, loop=args.loop)
    report = executor.report()
    run_log.close(f"运行结束：{result} · {report['steps']} 步，"
                  f"失败 {report['failed']}，耗时 {report['seconds']:g} 秒")
    if run_log.path:
        print(f"运行日志：{run_log.path}")

    try:
        path = _write_report(report, report_dir, result)
        if run_log.path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"\n运行日志：`{run_log.path}`\n")
        print(f"报告已写入：{path}")
    except (OSError, ValueError) as exc:
        print(f"警告：报告写入失败：{exc}")

    if args.data and executor.row_results:
        from rpa.engine import result_path, write_results

        result_file = result_path(args.data)
        try:
            write_results(data, executor.row_results, result_file)
            print(f"结果已写入：{result_file}")
        except (OSError, ValueError) as exc:
            print(f"警告：结果写入失败：{exc}")

    print(f"运行结束：{result}（{report['steps']} 步，"
          f"失败 {report['failed']}，{report['seconds']:g} 秒）")
    lock.release()
    return 0 if result in ("finished", "stopped") else 2


def main() -> int:
    logs.setup()
    argv = sys.argv[1:]
    # 带任何无头参数（含 -h/--help）都走命令行分支，否则启动界面
    headless = any(flag in argv for flag in
                   ("--run", "--all", "--list", "--loop", "--data",
                    "--from-row", "--report", "-h", "--help"))
    if headless:
        import argparse

        parser = argparse.ArgumentParser(
            description="MeowGo 无头运行（不弹界面）")
        parser.add_argument("--run", metavar="组名",
                            help="只运行指定的执行组")
        parser.add_argument("--all", action="store_true",
                            help="按顺序运行所有执行组")
        parser.add_argument("--loop", action="store_true",
                            help="循环运行直到手动停止")
        parser.add_argument("--data", metavar="文件",
                            help="数据文件（CSV/Excel），逐行驱动执行")
        parser.add_argument("--from-row", type=int, default=0, metavar="N",
                            help="数据驱动时跳过前 N 行，从第 N+1 行继续")
        parser.add_argument("--report", metavar="目录",
                            help="把运行报告（markdown）写到该目录"
                                 "（默认 reports/）")
        parser.add_argument("--list", action="store_true",
                            help="只列出状态文件里的执行组")
        args = parser.parse_args(argv)
        if not args.run and not args.all and not args.list:
            parser.error("无头模式需要 --run 组名 / --all / --list 之一")
        if args.from_row and not args.data:
            parser.error("--from-row 需要配合 --data 使用")
        if args.loop and args.data:
            parser.error("--loop 不能和 --data 一起用（数据驱动是按行跑一遍）")
        if not _check_deps():
            return 1
        _warn_optional()
        return _run_headless(args)

    if not _check_deps():
        return 1
    _warn_optional()

    from rpa.gui import enable_dpi_awareness
    from rpa.gui import main as gui_main

    enable_dpi_awareness()
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
