# -*- coding: utf-8 -*-
"""脚本执行环境：「环境:脚本|参数」→ 可直接启动的命令行。

内置环境（Windows / macOS / Linux 统一写法）：
    python / py / python3   Python 脚本
    powershell / ps1        Windows PowerShell
    pwsh                    PowerShell 7+
    cmd / bat               Windows 批处理
    bash / sh               Shell 脚本
    node / js               Node.js 脚本
    auto                    按脚本扩展名自动选择

高级用法：在程序目录放一个 ``script_envs.json``，即可登记自定义环境，
例如指定某个虚拟环境的解释器、额外的环境变量或工作目录::

    {
      "py311": {
        "exe": "C:\\\\Python311\\\\python.exe",
        "args": ["-u", "{script}"],
        "env": {"PYTHONIOENCODING": "utf-8"},
        "cwd": "",
        "shell": false,
        "extensions": [".py"]
      }
    }

字段说明（除 exe 外都可省略）：
    exe         解释器 / 可执行文件，写相对路径时按程序目录解析
    args        参数模板；``{script}`` 换成脚本路径，``{args}`` 展开为
                步骤里写的参数；没写 ``{script}`` 时自动追加脚本路径
    env         追加给子进程的环境变量
    cwd         工作目录（相对路径按程序目录解析；省略 = 脚本所在目录）
    shell       True 时用系统的 shell 解释命令（适合 uv / npm 这类包装命令）
    extensions  auto 环境按扩展名匹配时优先使用该自定义环境

步骤内容写法：``环境:脚本路径|参数1|参数2 =>变量名``
    ``|`` 的每一段是一个独立参数（段内空格保留，不需要额外加引号）；
    ``=>变量名`` 可选，用于把脚本的标准输出存进运行期变量。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

# 自定义环境配置文件（放在程序目录，界面里会在脚本步骤的提示中显示）
ENV_FILE_NAME = "script_envs.json"

# 内置环境别名 -> 规范名（大小写不敏感）
BUILTIN_ALIASES = {
    "python": "python", "py": "python", "python3": "python",
    "powershell": "powershell", "ps1": "powershell",
    "pwsh": "pwsh",
    "cmd": "cmd", "bat": "cmd", "cmd.exe": "cmd",
    "bash": "bash", "sh": "sh",
    "node": "node", "js": "node",
    "auto": "auto",
}

# auto 环境：脚本扩展名 -> 内置环境
EXTENSION_ENVS = {
    ".py": "python",
    ".ps1": "powershell",
    ".bat": "cmd", ".cmd": "cmd",
    ".sh": "bash",
    ".js": "node", ".mjs": "node", ".cjs": "node",
}


def app_dir() -> str:
    """程序所在目录（打包后是 exe 目录，源码运行时是项目根目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def custom_env_path() -> str:
    """自定义环境配置文件的完整路径。"""
    return os.path.join(app_dir(), ENV_FILE_NAME)


@dataclass
class ScriptSpec:
    """一条脚本步骤解析后的结果。"""

    env: str = "python"                 # 环境名（小写）
    script: str = ""                    # 脚本路径（可能还是相对路径）
    args: list[str] = field(default_factory=list)   # 每个元素是一个参数
    var: str = ""                       # 可选：把标准输出存进这个变量
    raw: str = ""                       # 原始写法（日志用）


# 自定义环境缓存：按文件修改时间失效，避免每个步骤都读一次磁盘
_custom_cache: dict[str, Any] = {"path": "", "mtime": None, "data": {}}


def _normalize_entry(raw: Any) -> Optional[dict[str, Any]]:
    """把配置文件里的一项整理成统一结构；无效项返回 None。"""
    if not isinstance(raw, dict):
        return None
    exe = str(raw.get("exe") or raw.get("command") or "").strip()
    if not exe:
        return None
    args = raw.get("args") or ["{script}"]
    if isinstance(args, str):
        args = [args]
    if not isinstance(args, list):
        args = ["{script}"]
    env = raw.get("env")
    if not isinstance(env, dict):
        env = {}
    extensions = raw.get("extensions")
    if not isinstance(extensions, list):
        extensions = []
    return {
        "exe": exe,
        "args": [str(item) for item in args],
        "env": {str(key): str(value) for key, value in env.items()},
        "cwd": str(raw.get("cwd") or ""),
        "shell": bool(raw.get("shell", False)),
        "extensions": [str(item).lower() for item in extensions
                       if str(item).strip()],
    }


def load_custom_envs() -> dict[str, dict[str, Any]]:
    """读取自定义环境；文件不存在或格式错误时返回空字典，不影响流程。"""
    path = custom_env_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _custom_cache.update({"path": path, "mtime": None, "data": {}})
        return {}
    if (_custom_cache["path"] == path
            and _custom_cache["mtime"] == mtime):
        return _custom_cache["data"]
    data: dict[str, dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        for name, item in raw.items():
            entry = _normalize_entry(item)
            if entry is not None:
                data[str(name).strip().lower()] = entry
    _custom_cache.update({"path": path, "mtime": mtime, "data": data})
    return data


def env_names() -> list[str]:
    """可用环境名（内置规范名 + 自定义），给界面提示和错误信息用。"""
    names = set(BUILTIN_ALIASES.values())   # 别名折叠成规范名，避免重复
    names.update(load_custom_envs())
    return sorted(names)


def parse_spec(text: str) -> ScriptSpec:
    """把步骤内容解析成 ScriptSpec；写法不对时抛 ValueError。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("脚本步骤要写成 环境:脚本路径|参数")
    env_text, sep, rest = raw.partition(":")
    env = env_text.strip().lower()
    if not sep or not env:
        raise ValueError("脚本步骤要写成 环境:脚本路径|参数，"
                         "如 python:tools/导出.py|--day|7")
    rest = rest.strip()
    var = ""
    if "=>" in rest:
        rest, _, var = rest.rpartition("=>")
        var = var.strip()
        rest = rest.rstrip()
        if not var:
            raise ValueError("=> 后面要写变量名，如 python:a.py =>结果")
    parts = [part.strip() for part in rest.split("|")]
    script = parts[0]
    if not script:
        raise ValueError("脚本路径不能为空")
    # 空参数直接丢掉，避免手误多打一个 | 就传进一个空字符串
    args = [part for part in parts[1:] if part]
    return ScriptSpec(env=env, script=script, args=args, var=var, raw=raw)


def _resolve_executable(exe: str) -> str:
    """解释器写相对路径时按程序目录解析；只写名字时交给 PATH 查找。"""
    path = os.path.expanduser(os.path.expandvars(exe))
    if os.path.isabs(path) or "/" in path or "\\" in path:
        if not os.path.isabs(path):
            path = os.path.join(app_dir(), path)
        return os.path.normpath(path)
    return path


def _python_executable() -> str:
    """当前可用的 Python 解释器。

    源码运行时直接用 sys.executable（就是装了依赖的那个解释器）；
    打包成 exe 后 sys.executable 指向 MeowGo 自己，必须改找 PATH 里的
    python / py，找不到就提示用户改用自定义环境。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    for name in ("python", "py", "python3"):
        found = shutil.which(name)
        if found:
            return found
    raise ValueError("未找到 Python 解释器：请把 python 加入 PATH，"
                     f"或在 {ENV_FILE_NAME} 中登记解释器路径")


def resolve_script_path(script: str, base_dir: str = "") -> str:
    """解析脚本路径：绝对路径原样使用，相对路径依次在图片目录、
    当前工作目录、程序目录里查找，最后回退到第一个候选（便于报错）。"""
    path = os.path.expanduser(os.path.expandvars(script.strip().strip('"')))
    if not path:
        raise ValueError("脚本路径不能为空")
    if os.path.isabs(path):
        return os.path.normpath(path)
    candidates = []
    if base_dir:
        candidates.append(os.path.join(base_dir, path))
    candidates.append(os.path.abspath(path))
    candidates.append(os.path.join(app_dir(), path))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.normpath(candidate)
    return os.path.normpath(candidates[0])


def resolve_env(name: str) -> tuple[str, Optional[dict[str, Any]]]:
    """返回 (规范环境名, 自定义环境定义或 None)；未知环境抛 ValueError。"""
    key = (name or "").strip().lower()
    custom = load_custom_envs()
    if key in custom:                   # 同名时自定义环境优先
        return key, custom[key]
    builtin = BUILTIN_ALIASES.get(key)
    if builtin:
        return builtin, None
    raise ValueError(f"未知的脚本环境 {name!r}；可用："
                     + "、".join(env_names()))


def _auto_env(script: str) -> tuple[str, Optional[dict[str, Any]]]:
    """按扩展名推断环境；自定义环境的 extensions 优先。"""
    extension = os.path.splitext(script)[1].lower()
    for name, entry in load_custom_envs().items():
        if extension in entry["extensions"]:
            return name, entry
    builtin = EXTENSION_ENVS.get(extension)
    if not builtin:
        raise ValueError(f"无法根据扩展名 {extension or '（无）'} 判断脚本环境，"
                         "请写成 python:xxx 这类形式，"
                         f"或在 {ENV_FILE_NAME} 里配置 extensions")
    return builtin, None


def check_spec(text: str) -> str:
    """检查步骤写法；返回错误说明，空串表示没问题（不检查脚本是否存在）。"""
    try:
        spec = parse_spec(text)
        name, _entry = resolve_env(spec.env)
        if name == "auto":
            _auto_env(spec.script)
    except ValueError as exc:
        return str(exc)
    return ""


def _builtin_command(name: str, script: str,
                     args: list[str]) -> tuple[list[str], dict[str, str]]:
    """内置环境 -> (命令列表, 需要追加的环境变量)。"""
    if name == "python":
        return ([_python_executable(), "-u", script] + args,
                # 固定 UTF-8 输出，避免 Windows 中文乱码；-u 保证日志及时
                {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    if name == "powershell":
        return (["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", script] + args, {})
    if name == "pwsh":
        return (["pwsh", "-NoProfile", "-NonInteractive",
                 "-File", script] + args, {})
    if name == "cmd":
        if os.name != "nt":
            raise ValueError("cmd 环境只支持 Windows，其它系统请改用 bash / sh")
        return (["cmd", "/c", script] + args, {})
    if name == "bash":
        return (["bash", script] + args, {})
    if name == "sh":
        return (["sh", script] + args, {})
    if name == "node":
        return (["node", script] + args, {})
    raise ValueError(f"未知的脚本环境：{name}")


def _custom_command(entry: dict[str, Any], script: str,
                    args: list[str]) -> tuple[list[str], dict[str, str]]:
    """自定义环境 -> (命令列表, 追加的环境变量)。"""
    exe = _resolve_executable(entry["exe"])
    template = entry["args"]
    has_script = any("{script}" in item for item in template)
    has_args = any(item.strip() == "{args}" for item in template)
    command = [exe]
    for item in template:
        if item.strip() == "{args}":
            command.extend(args)
        else:
            command.append(item.replace("{script}", script))
    if not has_script:
        command.append(script)          # 模板没写 {script} 时自动补上
    if not has_args:
        command.extend(args)            # 模板没写 {args} 时参数追加在最后
    extra = dict(entry.get("env") or {})
    if os.path.basename(exe).lower().startswith("python"):
        extra.setdefault("PYTHONIOENCODING", "utf-8")
        extra.setdefault("PYTHONUNBUFFERED", "1")
    return command, extra


def _check_executable(exe: str) -> None:
    """确认解释器存在，不存在时给出可照做的提示。"""
    if os.path.isabs(exe) or "/" in exe or "\\" in exe:
        if not os.path.isfile(exe):
            raise ValueError(f"找不到脚本解释器：{exe}（请检查 {ENV_FILE_NAME}）")
        return
    if shutil.which(exe) is None:
        raise ValueError(f"系统中找不到 {exe!r}：请先安装并加入 PATH，"
                         f"或在 {ENV_FILE_NAME} 里指定解释器路径")


def build_command(spec: ScriptSpec, base_dir: str = ""
                  ) -> tuple[list[str], str, dict[str, str], bool]:
    """把 ScriptSpec 变成 Popen 能直接用的参数。

    返回 (命令列表, 工作目录, 环境变量, 是否用 shell)。
    脚本不存在 / 环境未知 / 解释器缺失都抛 ValueError。
    """
    name, entry = resolve_env(spec.env)
    if name == "auto":
        name, entry = _auto_env(spec.script)

    script = resolve_script_path(spec.script, base_dir)
    if not os.path.isfile(script):
        raise ValueError(f"脚本文件不存在：{script}")

    if entry is None:
        command, extra_env = _builtin_command(name, script, spec.args)
        cwd = os.path.dirname(script)
        shell = False
    else:
        command, extra_env = _custom_command(entry, script, spec.args)
        if entry["cwd"]:
            cwd = os.path.expanduser(os.path.expandvars(entry["cwd"]))
            if not os.path.isabs(cwd):
                cwd = os.path.join(app_dir(), cwd)
            cwd = os.path.normpath(cwd)
        else:
            cwd = os.path.dirname(script)
        shell = bool(entry["shell"])

    _check_executable(command[0])
    env = os.environ.copy()
    env.update(extra_env)
    return command, cwd, env, shell
