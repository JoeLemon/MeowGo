#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MeowGo 跨平台打包脚本 —— Windows PowerShell / macOS / Linux 通用。

只需本机有 Python（3.9+），在任意终端里执行：

    Windows  (PowerShell) : python build.py
    macOS / Linux         : python3 build.py

流程：检查 Python 与 tkinter → 创建虚拟环境（可跳过）→ 安装依赖 → 生成平台图标
→ PyInstaller 打包 → 输出摘要。

常用选项：
    --no-venv       直接用当前 Python 环境打包（不创建 .venv）
    --venv 目录     自定义虚拟环境目录（默认 .venv）
    --onefile       打成单文件（默认单文件夹，启动更快）
    --name 名称     自定义产物名（默认 MeowGo）
    --icon 图片     自定义图标源（默认 main.png；Windows 自动转 .ico，macOS 转 .icns）
    --clean         打包前清空 build/ 与 dist/
    --skip-deps     跳过依赖安装（已装好时加速）
    --mirror 地址   指定 pip 镜像，如 https://pypi.tuna.tsinghua.edu.cn/simple
    --launcher      额外生成双击启动脚本（macOS .command / Linux .sh）
    --verify        打包后运行产物 --help 做冒烟自检
    --distpath 目录 PyInstaller 输出目录（默认 dist）
    -h              查看全部选项

产物位置：
    单文件夹（默认）
        Windows  dist/<名称>/<名称>.exe
        macOS    dist/<名称>/<名称>（另附 dist/<名称>.app，可用 MEOWGO_NO_APP=1 关闭）
        Linux    dist/<名称>/<名称>
    单文件（--onefile）
        Windows  dist/<名称>.exe
        macOS    dist/<名称>（另附 dist/<名称>.app，可用 MEOWGO_NO_APP=1 关闭）
        Linux    dist/<名称>
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time

MIN_PYTHON = (3, 9)
APP_NAME_DEFAULT = "MeowGo"
SPEC_FILE = "MeowGo.spec"
REQUIREMENTS = "requirements.txt"
ICON_SRC_DEFAULT = "main.png"
ICON_DIR = "build_assets"
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48),
              (64, 64), (128, 128), (256, 256)]

# 界面是 tkinter 写的，缺了它打出来的包没有界面，必须提前拦住
RUNTIME_MODULES = ["tkinter", "PIL", "pyautogui", "cv2",
                   "openpyxl", "xlrd", "pyperclip"]

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 基础工具

def platform_label() -> str:
    if sys.platform.startswith("win"):
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    log(f"\n==> {msg}")


def fail(msg: str, code: int = 1) -> "None":
    log(f"\n[失败] {msg}")
    sys.exit(code)


def run(cmd: list, env: dict | None = None) -> None:
    """执行外部命令，失败即中止并给出可复制执行的命令行。"""
    shown = " ".join("<内联脚本>" if "\n" in str(c) else str(c) for c in cmd)
    log(f"    $ {shown}")
    proc = subprocess.run([str(c) for c in cmd], cwd=PROJECT_ROOT, env=env)
    if proc.returncode != 0:
        fail(f"命令执行失败（退出码 {proc.returncode}）：\n    {shown}")


def venv_python(venv_dir: str) -> str:
    if sys.platform.startswith("win"):
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


# ---------------------------------------------------------------- 各步骤

def check_python() -> None:
    step("检查 Python 版本")
    log(f"    解释器: {sys.executable}")
    log(f"    版本  : {platform.python_version()}（{platform_label()}）")
    if sys.version_info < MIN_PYTHON:
        fail(f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+，当前版本过低")
    log("    [OK] 版本满足要求")


def resolve_interpreter(args) -> str:
    step("准备打包环境")
    if args.no_venv:
        log("    --no-venv：直接使用当前环境")
        return sys.executable
    venv_dir = os.path.abspath(args.venv)
    if not os.path.exists(venv_dir):
        log(f"    创建虚拟环境: {venv_dir}")
        try:
            run([sys.executable, "-m", "venv", venv_dir])
        except SystemExit:
            fail("虚拟环境创建失败。\n"
                 "    Debian/Ubuntu 请先安装: sudo apt install python3-venv\n"
                 "    或改用 --no-venv 直接在当前环境打包")
    else:
        log(f"    复用已有虚拟环境: {venv_dir}")
    py = venv_python(venv_dir)
    if not os.path.exists(py):
        fail(f"虚拟环境里找不到 Python：{py}\n    请删除 {venv_dir} 后重试，或使用 --no-venv")
    return py


def install_deps(python_exe: str, args) -> None:
    step("安装依赖")
    if not os.path.exists(os.path.join(PROJECT_ROOT, REQUIREMENTS)):
        fail(f"找不到 {REQUIREMENTS}，请在项目根目录运行本脚本")
    cmd = [python_exe, "-m", "pip", "install", "-r", REQUIREMENTS, "pyinstaller"]
    if args.mirror:
        cmd += ["-i", args.mirror]
    if args.skip_deps:
        log("    --skip-deps：跳过安装（请确认依赖已就绪）")
        return
    run(cmd)
    log("    [OK] 依赖安装完成")


def check_runtime(python_exe: str, args) -> None:
    """预检 tkinter 与运行依赖——缺 tkinter 会打出没有界面的包，必须提前拦住。"""
    step("检查运行环境")
    snippet = (
        "import importlib\n"
        f"mods = {RUNTIME_MODULES!r}\n"
        "missing = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except ImportError:\n"
        "        missing.append(m)\n"
        "print('MISSING:' + ','.join(missing))\n"
    )
    proc = subprocess.run([python_exe, "-c", snippet], cwd=PROJECT_ROOT,
                          capture_output=True, text=True)
    missing: list = []
    for line in proc.stdout.splitlines():
        if line.startswith("MISSING:"):
            missing = [m for m in line[len("MISSING:"):].split(",") if m]
    if "tkinter" in missing:
        if sys.platform.startswith("win"):
            hint = ("当前 Python 未安装 tkinter（界面库）。请改用 python.org 的完整版 "
                    "Python（安装时勾选 tcl/tk），或执行 "
                    "pip install tk 后重试。\n"
                    "    注意：虚拟环境无法弥补基础 Python 缺少的 tkinter，"
                    "需换解释器后重建虚拟环境。")
        elif sys.platform == "darwin":
            hint = ("当前 Python 未安装 tkinter（界面库）。请改用 python.org 官方 "
                    "安装包自带的 Python，或执行 brew install python-tk 后重试。")
        else:
            hint = ("当前 Python 未安装 tkinter（界面库）。Debian/Ubuntu 请执行 "
                    "sudo apt install python3-tk，其他发行版安装对应的 tkinter 包。")
        fail(f"缺少 tkinter，无法打出带界面的包。\n    {hint}")
    rest = [m for m in missing if m != "tkinter"]
    if rest:
        if args.skip_deps:
            log(f"    [警告] 缺少运行依赖：{'、'.join(rest)}"
                "（--skip-deps 已跳过安装，产物可能无法运行）")
        else:
            fail(f"依赖安装后仍缺少：{'、'.join(rest)}，请检查 pip 输出")
    log(f"    [OK] tkinter 与 {len(RUNTIME_MODULES) - 1} 项运行依赖就绪")


def prepare_icon(python_exe: str, args) -> "str | None":
    """把图标源转成平台格式：Windows -> .ico（多尺寸），macOS -> .icns。"""
    step("生成平台图标")
    src = os.path.abspath(args.icon)
    if not os.path.exists(src):
        log(f"    [提示] 找不到图标源 {args.icon}，将不嵌入自定义图标")
        return None
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        log("    Linux 无需转换（PyInstaller 不嵌入图标）")
        return None
    os.makedirs(os.path.join(PROJECT_ROOT, ICON_DIR), exist_ok=True)
    ext = "ico" if sys.platform.startswith("win") else "icns"
    out = os.path.join(PROJECT_ROOT, ICON_DIR,
                       f"{os.path.splitext(os.path.basename(src))[0]}.{ext}")
    snippet = (
        "from PIL import Image\n"
        f"src, out = {src!r}, {out!r}\n"
        "im = Image.open(src).convert('RGBA')\n"
        "if max(im.size) > 512:  # icns 上限 512px\n"
        "    im.thumbnail((512, 512), Image.LANCZOS)\n"
        "if out.lower().endswith('.ico'):\n"
        f"    im.save(out, sizes={ICON_SIZES!r})\n"
        "else:\n"
        "    im.save(out)\n"
        "print('    [OK] 已生成 ' + out)\n"
    )
    probe = subprocess.run(
        [python_exe, "-c", "import PIL"], cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        log("    [提示] 当前环境没有 Pillow，跳过图标转换（spec 会回退到 main.png）")
        return None
    run([python_exe, "-c", snippet])
    return out


def clean_artifacts() -> None:
    step("清理旧产物")
    for name in ("build", "dist"):
        path = os.path.join(PROJECT_ROOT, name)
        if os.path.exists(path):
            shutil.rmtree(path)
            log(f"    已删除 {name}/")


def artifact_path(args) -> str:
    """返回产物主程序路径：单文件是 exe 本身，单文件夹是目录。"""
    root = os.path.join(PROJECT_ROOT, args.distpath)
    if args.onefile:
        suffix = ".exe" if sys.platform.startswith("win") else ""
        return os.path.join(root, args.name + suffix)
    return os.path.join(root, args.name)


def run_pyinstaller(python_exe: str, args, icon: "str | None") -> None:
    step("PyInstaller 打包")
    if not os.path.exists(os.path.join(PROJECT_ROOT, SPEC_FILE)):
        fail(f"找不到 {SPEC_FILE}，请在项目根目录运行本脚本")
    env = dict(os.environ)
    env["MEOWGO_APP_NAME"] = args.name
    env["MEOWGO_BUILD_MODE"] = "onefile" if args.onefile else "onedir"
    if icon:
        env["MEOWGO_ICON"] = icon
    run([python_exe, "-m", "PyInstaller", "--noconfirm",
         "--distpath", args.distpath, SPEC_FILE], env=env)


def write_launcher(args) -> "str | None":
    """生成双击启动脚本（macOS .command / Linux .sh）。"""
    if not args.launcher:
        return None
    step("生成启动脚本")
    target = artifact_path(args)
    if not os.path.exists(target):
        fail(f"找不到产物 {target}，无法生成启动脚本")
    if sys.platform.startswith("win"):
        log("    Windows 下直接双击 <名称>.exe 即可，无需启动脚本")
        return None
    if args.onefile:
        launcher_dir = os.path.dirname(target)
        exe_name = os.path.basename(target)
    else:
        launcher_dir = target
        exe_name = args.name
    suffix = "command" if sys.platform == "darwin" else "sh"
    path = os.path.join(launcher_dir, f"{args.name}.{suffix}")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/bin/bash\n"
                 'cd "$(dirname "$0")"\n'
                 f"exec ./{exe_name}\n")
    os.chmod(path, 0o755)
    log(f"    [OK] 已生成 {os.path.relpath(path, PROJECT_ROOT)}（双击即可启动）")
    return path


def verify_artifact(args) -> None:
    """运行产物 --help，确认能正常起来。"""
    step("冒烟自检")
    exe = artifact_path(args)
    if not os.path.exists(exe):
        fail(f"找不到产物 {exe}")
    log(f"    $ {exe} --help")
    proc = subprocess.run([exe, "--help"], cwd=os.path.dirname(exe),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        fail(f"自检失败：{exe} --help 返回退出码 {proc.returncode}")
    log("    [OK] 产物可正常启动")


def print_summary(args, started: float) -> None:
    step("打包完成")
    target = artifact_path(args)
    log(f"    平台   : {platform_label()} / {platform.machine()}")
    log(f"    模式   : {'单文件' if args.onefile else '单文件夹'}")
    if args.onefile:
        log(f"    单文件 : {target}")
    else:
        exe_name = args.name + (".exe" if sys.platform.startswith("win") else "")
        log(f"    目录   : {target}")
        log(f"    主程序 : {os.path.join(target, exe_name)}")
    if sys.platform == "darwin" and not os.environ.get("MEOWGO_NO_APP"):
        log(f"    应用包 : {os.path.join(PROJECT_ROOT, args.distpath, args.name + '.app')}"
            "（Finder 双击运行；MEOWGO_NO_APP=1 可关闭）")
    log(f"    耗时   : {time.time() - started:.1f} 秒")
    log("")
    log("  说明：执行组配置（rpa_settings.json）、截图（images/）会写在 exe 同目录；")
    log("        macOS 首次双击若提示「无法验证开发者」，请在 系统设置 → 隐私与安全性 中允许。")


# ---------------------------------------------------------------- 入口

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="MeowGo 跨平台打包脚本（Windows / macOS / Linux）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python build.py                 一键打包（默认单文件夹）\n"
               "  python build.py --onefile       打成单文件\n"
               "  python build.py --no-venv       用当前环境打包，跳过装依赖\n"
               "  python build.py --clean --verify 清场重打并自检\n")
    parser.add_argument("--no-venv", action="store_true",
                        help="不创建虚拟环境，直接使用当前 Python")
    parser.add_argument("--venv", default=".venv", metavar="目录",
                        help="虚拟环境目录（默认 .venv）")
    parser.add_argument("--onefile", action="store_true",
                        help="打成单文件（默认单文件夹，启动更快）")
    parser.add_argument("--name", default=APP_NAME_DEFAULT, metavar="名称",
                        help=f"产物名称（默认 {APP_NAME_DEFAULT}）")
    parser.add_argument("--icon", default=ICON_SRC_DEFAULT, metavar="图片",
                        help=f"图标源文件（默认 {ICON_SRC_DEFAULT}）")
    parser.add_argument("--clean", action="store_true",
                        help="打包前清空 build/ 与 dist/")
    parser.add_argument("--skip-deps", action="store_true",
                        help="跳过依赖安装")
    parser.add_argument("--mirror", metavar="地址",
                        help="pip 镜像源，如 https://pypi.tuna.tsinghua.edu.cn/simple")
    parser.add_argument("--launcher", action="store_true",
                        help="额外生成双击启动脚本（macOS .command / Linux .sh）")
    parser.add_argument("--verify", action="store_true",
                        help="打包后运行产物 --help 做冒烟自检")
    parser.add_argument("--distpath", default="dist", metavar="目录",
                        help="PyInstaller 输出目录（默认 dist）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    started = time.time()
    log(f"MeowGo 跨平台打包  [{platform_label()} / {platform.machine()}]")
    check_python()
    python_exe = resolve_interpreter(args)
    # 先装依赖再检查：新建的虚拟环境里 PIL/cv2 等一开始肯定不存在，
    # 反过来检查会把「还没装」误判成「装完仍缺」，导致全新环境永远打不了包。
    install_deps(python_exe, args)
    check_runtime(python_exe, args)
    icon = prepare_icon(python_exe, args)
    if args.clean:
        clean_artifacts()
    run_pyinstaller(python_exe, args, icon)
    write_launcher(args)
    if args.verify:
        verify_artifact(args)
    print_summary(args, started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
