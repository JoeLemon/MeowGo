# -*- coding: utf-8 -*-
"""程序入口：先检查依赖，再启动界面。

无头模式（给 Windows 任务计划程序用，不弹界面）：
    python start-app.py --run 组名 [--loop] [--data 数据.csv]
                        [--report 报告目录] [--list]
"""
from __future__ import annotations

import importlib
import os
import sys

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
    """把运行报告写成 markdown，返回文件路径。"""
    import time

    from rpa.engine import report_markdown

    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    path = os.path.join(directory, f"run_{stamp}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report_markdown(report, result))
    return path


def _run_headless(args) -> int:
    loaded = _load_project()
    if loaded is None:
        return 1
    project, state = loaded

    from rpa.engine import Executor, validate_project

    if args.list:
        print("执行组：")
        for group in project.groups:
            print(f"  {group.name}（{len(group.steps)} 步）")
        return 0

    groups = {group.name: group.steps for group in project.groups}
    if args.run:
        if args.run not in groups:
            print(f"找不到执行组：{args.run}")
            print("可用组：" + "、".join(groups))
            return 1
        entry = args.run
    else:
        entry = None
        if not groups:
            print("状态文件里还没有执行组。")
            return 1

    image_dir = state.get("image_dir", "images")
    problems = validate_project(project.groups, base_dir=image_dir,
                                check_images=True)
    if problems:
        print("运行前校验发现问题：")
        for problem in problems:
            print(f"  - {problem}")
        print("仍按当前配置继续执行……")

    data = _load_data(args.data) if args.data else []
    if args.data and not data:
        print(f"警告：数据文件 {args.data} 里没有数据行")

    # 状态文件里 delay / confidence 等设置是平铺保存的
    executor = Executor()
    executor.base_dir = image_dir
    executor.groups = groups
    executor.order = project.names()
    executor.confidence = float(state.get("confidence", 0.9))
    executor.data = data
    executor.log = lambda level, msg: print(f"[{level}] {msg}", flush=True)

    result = executor.run(entry, loop=args.loop)
    report = executor.report()

    if args.report:
        path = _write_report(report, args.report, result)
        print(f"报告已写入：{path}")

    print(f"运行结束：{result}（{report['steps']} 步，"
          f"失败 {report['failed']}，{report['seconds']:g} 秒）")
    return 0 if result in ("finished", "stopped") else 2


def main() -> int:
    argv = sys.argv[1:]
    # 带任何无头参数（含 -h/--help）都走命令行分支，否则启动界面
    headless = any(flag in argv for flag in
                   ("--run", "--all", "--list", "--loop", "--data",
                    "--report", "-h", "--help"))
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
        parser.add_argument("--report", metavar="目录",
                            help="把运行报告（markdown）写到该目录")
        parser.add_argument("--list", action="store_true",
                            help="只列出状态文件里的执行组")
        args = parser.parse_args(argv)
        if not args.run and not args.all and not args.list:
            parser.error("无头模式需要 --run 组名 / --all / --list 之一")
        if not _check_deps():
            return 1
        return _run_headless(args)

    if not _check_deps():
        return 1

    from rpa.gui import enable_dpi_awareness, main as gui_main

    enable_dpi_awareness()
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
