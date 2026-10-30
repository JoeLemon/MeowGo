# -*- coding: utf-8 -*-
"""MeowGo 客户端多语言支持（简体中文 / English）。

设计目标：
- 源码里继续写中文，界面显示时统一经过 ``tr()``，不把翻译散落到业务逻辑里；
- 语言切换后已存在的控件也能即时刷新，不需要重启客户端；
- 只翻译「源文案」，用户数据（执行组名、文件路径、Webhook 地址）保持原样。

对外接口：
    set_language("zh" | "en")
    get_language()
    tr(text)
    install()                 # 安装一次 Tk 控件拦截，App 启动时调用
    refresh(root)             # 语言切换后刷新整棵控件树
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Iterable

LANGUAGES: tuple[tuple[str, str], ...] = (("zh", "简体中文"), ("en", "English"))
LANGUAGE_ENGLISH_NAMES = {"zh": "Chinese (Simplified)", "en": "English"}
DEFAULT_LANGUAGE = "zh"

_state = {"language": DEFAULT_LANGUAGE}
_installed = False
_applying = False
_original_title = None
_source_attr = "_mg_i18n_source"

# ---------------------------------------------------------------------------
# 英文文案
# ---------------------------------------------------------------------------
# 键是源码里的中文原文；新增界面文案时，把对应英文补在这里即可。
EN: dict[str, str] = {
    # 通用
    "确定": "OK",
    "取消": "Cancel",
    "保存": "Save",
    "关闭": "Close",
    "删除": "Delete",
    "新增": "Add",
    "复制": "Copy",
    "清空": "Clear",
    "浏览": "Browse…",
    "开始运行": "Start",
    "运行这一步": "Run step",
    "跳过": "Skip",
    "重试": "Retry",
    "管理": "Manage",
    "历史": "History",
    "未知": "Unknown",
    "退出": "Exit",
    "就绪": "Ready",

    # 顶栏 / 菜单
    "文件": "File",
    "编辑": "Edit",
    "运行": "Run",
    "视图": "View",
    "帮助": "Help",
    "导入 Excel（单个组）…": "Import Excel (single group)…",
    "第一个工作表": "First worksheet",
    "导入 Excel（全部工作表）…": "Import Excel (all worksheets)…",
    "每表一个组": "One group per sheet",
    "导出当前组为 Excel…": "Export current group to Excel…",
    "导出所有组为 Excel…": "Export all groups to Excel…",
    "导出运行日志…": "Export run log…",
    "打开报告索引": "Open report index",
    "打开运行报告目录": "Open report folder",
    "恢复配置备份…": "Restore configuration backup…",
    "打开图片目录": "Open image folder",
    "新建执行组": "New group",
    "恢复预置执行组…": "Restore preset groups…",
    "补回缺失模板": "Restore missing templates",
    "重命名执行组…": "Rename group…",
    "复制为副本": "Duplicate",
    "删除执行组": "Delete group",
    "新增步骤": "Add step",
    "全选步骤": "Select all steps",
    "编辑步骤…": "Edit step…",
    "复制步骤": "Duplicate step",
    "删除步骤": "Delete step",
    "截取屏幕并替换图片…": "Capture screen and replace image…",
    "宏录制鼠标操作…": "Record mouse actions…",
    "录成坐标步骤": "Save as coordinate steps",
    "运行当前组": "Run current group",
    "循环运行当前组": "Loop current group",
    "运行全部组": "Run all groups",
    "带数据运行…": "Run with data…",
    "单步调试运行这个组": "Debug this group",
    "每步确认": "Confirm each step",
    "单步调试开关：开": "Step debug: On",
    "单步调试开关：关": "Step debug: Off",
    "定时运行…": "Scheduled runs…",
    "暂停 / 继续": "Pause / Resume",
    "停止": "Stop",
    "F9 全局": "F9 global",
    "收起运行日志": "Collapse run log",
    "展开运行日志": "Expand run log",
    "切换到深色主题": "Switch to dark theme",
    "切换到浅色主题": "Switch to light theme",
    "运行设置…": "Run settings…",
    "图片管家…": "Image manager…",
    "使用说明": "User guide",
    "快捷键速查": "Keyboard shortcuts",
    "关于 MeowGo": "About MeowGo",

    # 顶栏操作
    "定时任务：查看即将执行与倒计时": "Scheduled tasks: upcoming runs and countdown",
    "运行设置": "Run settings",
    "撤销（Ctrl+Z）": "Undo (Ctrl+Z)",
    "重做（Ctrl+Y）": "Redo (Ctrl+Y)",
    "单步": "Step",
    "单步调试：每步执行前确认（F8）": "Step debug: confirm before each step (F8)",
    "运行组": "Run group",
    "运行当前组 F5": "Run current group (F5)",
    "循环": "Loop",
    "循环运行当前组": "Loop current group",
    "暂停": "Pause",
    "暂停 / 继续": "Pause / Resume",
    "停止（全局热键 F9）": "Stop (global hotkey F9)",

    # 主界面
    "执行组": "Groups",
    "新建执行组（Ctrl+G）": "New group (Ctrl+G)",
    "双击重命名 · 拖拽排序 · 右键更多操作":
        "Double-click to rename · Drag to reorder · Right-click for more",
    "尚未创建执行组": "No groups yet",
    "执行组由一组可重复执行的步骤组成。\n请先创建执行组，再添加步骤。":
        "A group is a reusable sequence of steps.\nCreate a group first, then add steps.",
    "步骤": "Steps",
    "双击单元格可直接编辑": "Double-click a cell to edit",
    "新增步骤（Ctrl+Enter）": "Add step (Ctrl+Enter)",
    "清除搜索（Esc）": "Clear search (Esc)",
    "类型": "Type",
    "内容": "Content",
    "重复": "Repeat",
    "备注": "Note",
    "此执行组尚未添加步骤": "This group has no steps yet",
    "请添加第一个步骤，或通过右键菜单批量导入。":
        "Add the first step, or import steps from the right-click menu.",
    "在文件夹中显示": "Show in folder",
    "运行日志": "Run log",
    "收起 / 展开日志": "Collapse / expand log",
    "收起 / 展开日志": "Collapse / expand log",
    "复制全部日志到剪贴板": "Copy all logs to clipboard",
    "收起摘要": "Collapse summary",
    "打开报告": "Open report",
    "快捷键速查（?）": "Keyboard shortcuts (?)",
    "执行组自动保存": "Groups are saved automatically",
    "自动保存": "Auto-save",
    "定位到失败步骤": "Go to failed step",
    "在步骤表里标出失败的那一步": "Highlight the failed step in the table",
    "已保存 · ": "Saved · ",
    "未保存…": "Unsaved…",
    "已暂停": "Paused",
    "执行完成": "Completed",
    "执行出错": "Failed",
    "搜索步骤…": "Search steps…",
    "没有找到“": "No steps matching “",
    "”的步骤": "”",
    "换个关键词试试，或清除搜索查看全部步骤":
        "Try another keyword, or clear the search to show all steps.",
    "清除搜索": "Clear search",
    "当前组：": "Current group: ",
    " 步）": " steps)",
    " 个组": " groups",
    " 张": " images",

    # 空状态 / 提示
    "添加第一个步骤；也可以右键列表批量导入或粘贴":
        "Add the first step, or import or paste steps from the right-click menu.",
    "这里空空如也，喵～": "Nothing here yet",
    "已切换到深色主题": "Switched to dark theme",
    "已切换到浅色主题": "Switched to light theme",

    # 定时运行
    "定时运行与通知": "Scheduled runs and notifications",
    "定时任务": "Scheduled tasks",
    "新增一个定时任务": "Create a scheduled task",
    "开关可快速停用": "Use the row switch to enable or disable",
    "行内开关可快速启用或停用": "Use the row switch to enable or disable",
    "还没有定时任务\n点「新增」创建一个":
        "No scheduled tasks yet\nClick Add to create one.",
    "暂无定时任务\n请点击「新增」创建":
        "No scheduled tasks yet\nClick Add to create one.",
    "快速开启 / 关闭这个任务": "Enable or disable this task",
    "快速启用或停用此任务": "Enable or disable this task",
    "删除定时任务": "Delete scheduled task",
    "确定删除定时任务「": "Delete the scheduled task “",
    "任务设置": "Task settings",
    "修改后点底部「保存」生效；左侧开关可快速启停。":
        "Click Save below to apply changes. Use the row switch to enable or disable the task.",
    "每日运行时间": "Daily run time",
    "每天运行时间": "Daily run time",
    "时 : 分": "hh : mm",
    "运行哪个组": "Group",
    "目标执行组": "Target group",
    "结束通知": "Completion notification",
    "播放提示音": "Play notification sound",
    "填企业微信 / 钉钉机器人地址，运行结束自动推送；留空则不推送。结束通知为所有定时任务共用。":
        "Enter a WeCom or DingTalk bot URL to send a notification after each run; "
        "leave blank to disable. Completion notifications are shared by all tasks.",
    "无人值守": "Unattended run",
    "无人值守运行": "Unattended run",
    "交给 Windows 计划任务，关掉界面也会按时跑。\n不需要管理员权限，创建后可在「任务计划程序」里查看。":
        "Runs through Windows Task Scheduler even when the client is closed.\n"
        "Administrator rights are not required. View the task in Task Scheduler.",
    "由 Windows 任务计划程序执行，客户端关闭后仍会按时运行。\n无需管理员权限；创建后可在「任务计划程序」中查看。":
        "Runs through Windows Task Scheduler even when the client is closed.\n"
        "Administrator rights are not required. View the task in Task Scheduler.",
    "创建计划任务": "Create task",
    "用当前任务的时间和执行组创建每天运行的计划任务":
        "Create a daily Windows task with this task's time and group",
    "删除计划任务": "Delete task",
    "还没有定时任务": "No scheduled tasks",
    "点左侧「新增」创建；开关可快速启停。":
        "Click Add on the left to create one. Use the switch to enable or disable it.",
    " 个任务": " tasks",
    "还没有执行组": "No groups yet",
    "请先新建并保存一个执行组。": "Create and save a group first.",
    "执行组不存在": "Group not found",
    "」还没保存，请先保存项目。": "” has not been saved. Save the project first.",
    "创建失败": "Creation failed",
    "调用 schtasks 失败：": "Failed to call schtasks: ",
    "已创建：": "Created: ",
    "（每天 ": " (daily at ",
    "Windows 计划任务已创建：": "Windows scheduled task created: ",
    "创建失败：": "Creation failed: ",
    "schtasks 返回错误": "schtasks returned an error",
    "确定删除 Windows 计划任务「":
        "Delete the Windows scheduled task “",
    "」？\n\n只删除系统计划任务，不影响软件里的定时设置。":
        "”?\n\nOnly the Windows task will be deleted; the in-app schedule stays unchanged.",
    "删除失败": "Deletion failed",
    "已删除：": "Deleted: ",
    "Windows 计划任务已删除：": "Windows scheduled task deleted: ",
    "删除失败：": "Deletion failed: ",
    "即将执行的定时任务": "Upcoming scheduled tasks",
    "打开定时运行与通知": "Open scheduled runs and notifications",
    "查看近期定时执行情况": "View recent scheduled runs",
    "历史保留最近 60 次；界面开着才会到点运行。":
        "The last 60 records are kept. Tasks run while the client is open.",
    "还没有启用的定时任务": "No enabled scheduled tasks",
    "还有 ": "And ",
    " 个任务…": " more tasks…",
    "定时执行历史": "Scheduled run history",
    "触发时间": "Triggered at",
    "任务": "Task",
    "结果": "Result",
    "执行情况": "Details",
    "正在执行…": "Running…",
    " 步 · 失败 ": " steps · failed ",
    "近 ": "Last ",
    " 次": " records",
    " · 最近 ": " · latest ",
    "还没有定时执行记录": "No scheduled runs yet",
    "运行中": "Running",
    "成功": "Succeeded",
    "已停止": "Stopped",
    "出错": "Failed",
    "已跳过": "Skipped",

    # 设置
    "启动倒计时（秒）": "Start countdown (seconds)",
    "启动前倒计时（秒）": "Start countdown (seconds)",
    "图片相似度": "Image match confidence",
    "图像匹配相似度": "Image match confidence",
    "图片目录": "Image folder",
    "图像目录": "Image folder",
    "运行时显示迷你进度条": "Show floating progress bar while running",
    "运行时显示悬浮进度条": "Show floating progress bar while running",
    "失败时保存截图": "Save a screenshot on failure",
    "失败时保存屏幕截图": "Save a screenshot on failure",
    "数据跑完写结果文件": "Write a result file after processing",
    "数据处理完成后写入结果文件": "Write a result file after processing",
    "开机自动启动": "Start the client at sign-in",
    "开机时自动启动客户端": "Start the client at sign-in",
    "启动后最小化到托盘": "Minimize to the system tray at startup",
    "启动后最小化至系统托盘": "Minimize to the system tray at startup",
    "关闭窗口时最小化到托盘": "Minimize to the system tray when closing",
    "关闭窗口时最小化至系统托盘": "Minimize to the system tray when closing",
    "界面语言": "Interface language",
    "更改后立即生效": "Changes take effect immediately",
    "相似度越低越容易识别，也越容易误点。失败截图在 reports/shots；数据结果写在数据文件旁边，下次运行同一份数据时可以选择从上次停下的地方继续。":
        "A lower confidence threshold improves recognition but increases false matches. "
        "Failure screenshots are saved in reports/shots; result files are written next to "
        "the data file. When rerunning the same data, you can resume from the last completed row.",
    "相似度阈值越低，识别范围越宽，误匹配风险也越高。失败截图保存于 reports/shots；数据结果写入数据文件所在目录。再次运行同一数据文件时，可选择从上次中断处继续。":
        "A lower confidence threshold improves recognition but increases false matches. "
        "Failure screenshots are saved in reports/shots; result files are written next to "
        "the data file. When rerunning the same data, you can resume from the last completed row.",
    "开机自启写在当前用户的注册表 Run 项里，不需要管理员权限；登录 Windows 后只打开界面，不会自动跑流程（到点无人值守跑流程请用「定时运行 → 创建计划任务」）。启动命令：":
        "Startup is registered in the current user's Run key and does not require "
        "administrator rights. Signing in only opens the client; it does not run a workflow "
        "automatically. For unattended scheduled runs, use Scheduled runs → Create scheduled task. "
        "Launch command: ",
    "开机自启通过当前用户注册表 Run 项实现，无需管理员权限。登录 Windows 后仅启动客户端，不自动执行流程；如需定时无人值守运行，请使用「定时运行 → 创建计划任务」。启动命令：":
        "Startup is registered in the current user's Run key and does not require "
        "administrator rights. Signing in only opens the client; it does not run a workflow "
        "automatically. For unattended scheduled runs, use Scheduled runs → Create scheduled task. "
        "Launch command: ",
    "托盘选项：勾选后启动或关闭窗口都只是收起界面，右下角托盘会出现 MeowGo 图标——双击打开窗口，右键菜单里可以真正退出。开机自启 + 启动到托盘 = 登录后安静待在后台；两个开关都取消并保存，托盘图标随即消失。":
        "When enabled, starting or closing the client only hides the window, and a MeowGo icon "
        "appears in the system tray. Double-click the icon to reopen the window; use its context "
        "menu to exit. Start at sign-in + minimize to tray keeps the client running quietly in the "
        "background. Turning both switches off and saving removes the tray icon.",
    "托盘选项：启用后，启动或关闭客户端只会隐藏窗口，系统托盘将显示 MeowGo 图标。双击图标可重新打开窗口，右键菜单可退出客户端。开机自启与启动后最小化至托盘同时启用时，客户端将在登录后静默驻留后台；关闭这两个选项并保存后，托盘图标将消失。":
        "When enabled, starting or closing the client only hides the window, and a MeowGo icon "
        "appears in the system tray. Double-click the icon to reopen the window; use its context "
        "menu to exit. Start at sign-in + minimize to tray keeps the client running quietly in the "
        "background. Turning both switches off and saving removes the tray icon.",
    "选择图片目录": "Select image folder",
    "开机自启开启失败": "Failed to enable startup",
    "开机自启已开启：": "Startup enabled: ",
    "开机自启关闭失败": "Failed to disable startup",
    "开机自启已关闭（已移除注册表启动项）":
        "Startup disabled (Run entry removed from the registry)",
    "启动倒计时": "Start countdown",

    # 步骤对话框
    "执行组名称": "Group name",
    "尽量简短，便于在「调用执行组」里引用":
        "Keep it short so it is easy to reference in Call group steps.",
    "指令类型": "Step type",
    "截图": "Capture",
    "选图": "Choose image",
    "预览": "Preview",
    "查看内容框里当前写的图片": "Preview the image currently named in the content field",
    "按键录入：直接按组合键，如 Ctrl+Shift+A":
        "Key capture: press the key combination, e.g. Ctrl+Shift+A",
    "拾取": "Pick",
    "把鼠标移到目标位置，点此倒计时拾取坐标":
        "Move the pointer to the target, then click to capture coordinates after a countdown",
    "满足后执行": "Run when matched",
    "选组": "Choose group",
    "选择条件满足时要执行的执行组": "Choose the group to run when the condition matches",
    "没出现时执行": "Run when not found",
    "选择没出现图片时要执行的执行组":
        "Choose the group to run when the image is not found",
    "重复次数": "Repeat count",
    "高级选项": "Advanced options",
    "识别范围、第几个、灰度、等待、重试、输入方式":
        "Region, occurrence, grayscale, wait, retry, input method",
    "等待": "Wait",
    "秒（0 = 一直等）": "seconds (0 = wait indefinitely)",
    "超时后": "On timeout",
    "跳过这步": "Skip this step",
    "停止执行": "Stop the run",
    "识别范围": "Region",
    "框选": "Select region",
    "在屏幕上拖拽选择识别区域": "Drag on the screen to select the match region",
    "留空 = 全屏查找；格式 x,y,宽,高": "Leave blank to search the full screen; format x,y,width,height",
    "匹配 / 重试": "Match / retry",
    "第": "Occurrence",
    "个": "",
    "灰度识别：颜色变化大时更稳": "Grayscale matching is more stable when colors vary",
    "灰度": "Grayscale",
    "失败重试": "Retry on failure",
    "次，间隔": "times, every",
    "秒": "seconds",
    "其他": "Other",
    "逐字输入（英文/数字）": "Type character by character (English/numbers)",
    "粘贴（快，支持中文）": "Paste (fast, supports Chinese)",
    "检测图片": "Test image",
    "隐藏窗口后检查当前屏幕能否找到图片":
        "Hide the window and check whether the image can be found on screen",
    "选择图片": "Choose image",
    "图片": "Image",
    "内容框还是空的，先截图或选一张图片":
        "The content field is empty. Capture or choose an image first.",
    "等待超时必须是数字": "Wait timeout must be a number.",
    "「第几个」要是 1 以上的整数": "Occurrence must be an integer greater than or equal to 1.",
    "重试次数必须是整数": "Retry count must be an integer.",
    "重试间隔必须是数字": "Retry interval must be a number.",
    "逐字": "Type",
    "还没有其它执行组，请先新建": "No other groups exist. Create one first.",
    "将执行组：": "Will run group: ",
    "没出现时将执行组：": "Will run group when not found: ",
    "3 秒后拾取当前鼠标位置，请把鼠标移到目标点…":
        "Capturing the pointer position in 3 seconds. Move the pointer to the target…",
    "已拾取坐标 (": "Coordinates captured (",
    "拾取失败：": "Capture failed: ",
    "出现时执行": "Run when found",
    "命中检查": "Test match",
    "当前时间": "Current time",
    "已录入：": "Captured: ",
    "（可继续手动修改）": " (you can still edit it manually)",
    "已填入截图；可点「预览」核对，或到「高级选项」点「检测图片」确认当前屏幕能命中":
        "Screenshot inserted. Use Preview to verify it, or Test image under Advanced options "
        "to confirm it matches on the current screen.",
    "拖拽：": "Drag: ",
    "拖拽起点：": "Drag start: ",
    "（还要填终点）": " (end point required)",
    "检查失败": "Test failed",
    "找不到图片文件：": "Image file not found: ",
    "当前屏幕未找到匹配图像（请确认目标窗口已打开且缩放比例一致）":
        "No matching image was found on screen. Confirm the target window is open "
        "and the display scaling matches.",
    "找到 1 处匹配 ✓": "1 match found ✓",
    "检查失败：": "Test failed: ",
    "先填写图片名": "Enter an image name first.",
    "识别范围格式不对，要写 x,y,宽,高": "Invalid region format. Use x,y,width,height.",
    "检查中…": "Testing…",
    "基础": "Basics",
    "（还没有其它组）": "(no other groups)",
    "可调用：": "Available groups: ",
    "也可点右侧键盘按钮直接按组合键录入（Ctrl+Shift+A 这种）":
        "You can also use the keyboard button on the right to capture a combination (e.g. Ctrl+Shift+A).",
    "左边填判断用图片，右边填满足后要执行的组（可点「选组」）":
        "Enter the condition image on the left and the group to run on match on the right "
        "(click Choose group).",
    "下面两个输入框分别填出现时 / 没出现时要执行的组（都能点「选组」）":
        "Enter the groups to run when found and when not found (both support Choose group).",
    "设置后，任意步骤的内容里都能用 {变量名} 读回":
        "After setting, any step can read the value with {variable name}.",
    "先手动复制好文字，这一步把它存进指定变量（发一次 Ctrl+C，剪贴板稍后会还原）":
        "Copy the text manually first. This step stores it in the specified variable "
        "(it sends Ctrl+C once and restores the clipboard afterwards).",
    "截图 / 选图 / 拾取会填进第一个空着的一段":
        "Capture, Choose image, or Pick fills the first empty segment.",
    "标题写窗口名的一部分就行，如 activate:记事本":
        "Use part of the window title, e.g. activate:Notepad.",
    "也可以把鼠标移到目标处，点「拾取」自动填坐标":
        "You can also move the pointer to the target and click Pick to fill the coordinates.",
    "默认用剪贴板粘贴（支持中文）\n个别应用不认粘贴时，可在「高级选项」改逐字输入":
        "Pastes through the clipboard by default (supports Chinese).\n"
        "If an application does not accept paste, switch to character-by-character input "
        "under Advanced options.",
    "截图 / 选图后点「预览」核对内容\n点错了可在「高级选项」里框选范围或指定第几个":
        "After capturing or choosing an image, click Preview to verify it.\n"
        "If it targets the wrong element, set a region or occurrence under Advanced options.",
    "「高级选项」里可以设置最多等几秒":
        "Set the maximum wait time under Advanced options.",

    # 图片 / 数据 / 调试 / 快捷键
    "预览 · ": "Preview · ",
    "预览失败": "Preview failed",
    "打不开图片：": "Cannot open image: ",
    " 像素": " px",
    " · 已缩放至 ": " · scaled to ",
    "图片管家": "Image manager",
    "图片文件": "Image file",
    "被谁引用": "Referenced by",
    "大小": "Size",
    "重命名并同步引用": "Rename and update references",
    "清理未引用图片": "Clean unreferenced images",
    "打开目录": "Open folder",
    "（未引用）": "(unreferenced)",
    "共 ": "Total ",
    " 张图片，": " images, ",
    " 张未被引用": " unreferenced",
    "；图片目录：": "; image folder: ",
    "（目录为空或不存在：": "(folder is empty or missing: ",
    "重命名图片 ": "Rename image ",
    "目标文件名已存在": "Target file name already exists",
    "重命名失败": "Rename failed",
    "已重命名为 ": "Renamed to ",
    "，同步 ": ", updated ",
    " 处引用": " references",
    "没有未被引用的图片": "No unreferenced images",
    "确认清理": "Confirm cleanup",
    "将删除 ": "The following ",
    " 张未被引用的图片：\n": " unreferenced images will be deleted:\n",
    "部分失败": "Partially failed",
    "这些文件没删掉：\n": "These files could not be deleted:\n",
    "已清理 ": "Cleaned ",
    " 张未引用图片": " unreferenced images",
    "带数据运行 · 预览": "Run with data · Preview",
    "文件：": "File: ",
    " 行数据 · ": " rows · ",
    " 列：": " columns: ",
    "（无）": "(none)",
    "（只显示前 6 列，共 ": "(showing the first 6 columns of ",
    " 列）": ")",
    "步骤内容里写 {列名}，运行时会被替换成当前行的值；{index} 是行号。":
        "Use {column name} in step content; it is replaced with the current row value at runtime. "
        "{index} is the row number.",
    "单步调试": "Step debug",
    "（无内容）": "(no content)",
    "重复 ": "Repeat ",
    "运行：执行这一步\u3000·\u3000跳过：不执行\u3000·\u3000停止：结束整个运行（Esc）":
        "Run: execute this step  ·  Skip: do not execute  ·  Stop: end the run (Esc)",
    "全选当前组的步骤（可批量删除/复制/移动）":
        "Select all steps in the current group (for batch delete/copy/move)",
    "编辑选中的步骤": "Edit selected steps",
    "删除选中的步骤": "Delete selected steps",
    "运行当前组（全局，任何时候有效）": "Run current group (global, always available)",
    "单步调试开关（每步执行前确认）": "Toggle step debug (confirm before each step)",
    "停止（全局，任何时候有效）": "Stop (global, always available)",
    "深色 / 浅色主题切换": "Switch between dark and light themes",
    "撤销 / 重做": "Undo / Redo",
    "打开本速查卡": "Open this shortcut card",
    "关闭对话框 / 取消倒计时": "Close dialog / cancel countdown",
    "运行安全：将鼠标快速移至屏幕左上角，可立即中断执行。":
        "Safety: move the pointer quickly to the top-left corner of the screen to "
        "abort execution immediately.",

    # 通知 / 托盘 / 系统消息
    "提示": "Notice",
    "还没有配置备份": "No configuration backup available",
    "恢复失败": "Restore failed",
    "恢复配置备份": "Restore configuration backup",
    "已恢复配置备份：": "Configuration backup restored: ",
    "打开失败": "Open failed",
    "重名": "Duplicate name",
    "执行组「": "Group “",
    "」已存在": "” already exists",
    "已新建执行组：": "Group created: ",
    "预置执行组": "Preset groups",
    "所有预置执行组都已存在。": "All preset groups already exist.",
    "恢复预置执行组": "Restore preset groups",
    "将补回 ": "Restore ",
    " 个预置执行组：\n": " preset groups:\n",
    "\n\n已有同名组会保留，不会覆盖。是否继续？":
        "\n\nExisting groups with the same name will be kept. Continue?",
    "已恢复预置执行组：": "Preset groups restored: ",
    "已恢复 ": "Restored ",
    " 个预置执行组": " preset groups",
    "重命名执行组": "Rename group",
    "」已重命名为「": "” renamed to “",
    "复制执行组": "Duplicate group",
    "副本": "copy",
    "已复制为执行组：": "Group duplicated: ",
    "确认删除": "Confirm deletion",
    "确定删除执行组「": "Delete group “",
    "」及其 ": "” and its ",
    " 个步骤？": " steps?",
    "已删除执行组：": "Group deleted: ",
    "移动执行组": "Move group",
    "已新增步骤：": "Step added: ",
    "请先在右侧选择一个步骤": "Select a step on the right first.",
    "编辑步骤": "Edit step",
    "已复制步骤": "Step duplicated.",
    "确定删除第 ": "Delete step ",
    " 步（": " (",
    "确定删除选中的 ": "Delete the selected ",
    "已删除 ": "Deleted ",
    "移动步骤": "Move step",
    "已移动 ": "Moved ",
    "没有可撤销的操作": "Nothing to undo.",
    "已撤销：": "Undone: ",
    "没有可重做的操作": "Nothing to redo.",
    "已重做：": "Redone: ",
    "撤销（无）": "Undo (none)",
    "重做（无）": "Redo (none)",
    "上移": "Move up",
    "下移": "Move down",
    "整块上移": "Move block up",
    "整块下移": "Move block down",
    "新增步骤…": "Add step…",
    "运行这个组": "Run this group",
    "循环运行这个组": "Loop this group",
    "新建执行组…": "New group…",
    "导入 Excel 配置": "Import Excel configuration",
    "Excel 文件": "Excel files",
    "所有文件": "All files",
    "导入失败": "Import failed",
    "该文件没有可导入的数据": "The file contains no importable data.",
    "导入 Excel": "Import Excel",
    "已导入 ": "Imported ",
    " 个执行组：": " groups: ",
    "导出为 Excel": "Export to Excel",
    "所有执行组.xlsx": "all-groups.xlsx",
    "Excel 工作簿": "Excel workbook",
    "导出失败": "Export failed",
    "已导出 ": "Exported ",
    " 个执行组到 ": " groups to ",
    "已导出到 ": "Exported to ",
    "拖拽框选识别范围（只记坐标，不截图），Esc 取消":
        "Drag to select the match region (coordinates only, no screenshot). Press Esc to cancel.",
    "按住鼠标拖拽框选要识别的区域，Esc 取消":
        "Drag to select the match region. Press Esc to cancel.",
    "框选区域太小，已取消": "Selected region is too small; canceled.",
    "截图失败": "Screenshot failed",
    "已保存截图：": "Screenshot saved: ",
    "不能使用图片。\n请选中一个图片类步骤，或在「新增步骤」里选择图片类指令。":
        " cannot use an image.\nSelect an image-based step, or choose an image instruction in Add step.",
    "日志为空，没有可复制的内容": "The log is empty; nothing to copy.",
    " 行日志到剪贴板": " log lines copied to clipboard.",
    "配置有误": "Invalid configuration",
    "正在运行中，请先停止": "A run is already in progress. Stop it first.",
    "选择数据文件（第一行是表头）": "Select a data file (first row is the header)",
    "表格文件": "Spreadsheet files",
    "读取失败": "Read failed",
    "读不出数据：": "Cannot read data: ",
    "这个文件里没有数据行": "The file contains no data rows.",
    "继续上次？": "Resume previous run?",
    "执行已完成": "Run already completed",
    "已经跑完": "Already completed",
    "还没有可执行的步骤": "There are no executable steps.",
    "即将开始，请切换到目标窗口（Esc 取消）":
        "Starting soon. Switch to the target window (Esc to cancel).",
    "启动倒计时被取消": "Startup countdown canceled.",
    "启动被新的运行取代": "Startup was replaced by a new run.",
    "已有其它运行在进行": "Another run is already in progress.",
    "已有运行在进行": "A run is already in progress.",
    "另一个 MeowGo 正在运行（可能是计划任务），\n等它结束后再试。":
        "Another MeowGo instance is running (possibly a scheduled task).\nTry again after it finishes.",
    "运行中…": "Running…",
    "（循环）": " (loop)",
    "（单步调试）": " (step debug)",
    "单步调试：每一步执行前都会确认":
        "Step debug: each step requires confirmation before execution.",
    "循环运行中…": "Looping…",
    "步数不可估…": "Estimating steps…",
    "已执行 ": "Completed ",
    "已开启": "enabled",
    "已关闭": "disabled",
    "（下次运行生效）": " (takes effect on the next run)",
    "正在停止…": "Stopping…",
    "定时运行跳过：找不到执行组「": "Scheduled run skipped: group “",
    "找不到执行组「": "Group “",
    "定时运行触发：当前组": "Scheduled run started: current group",
    "定时运行触发：": "Scheduled run started: ",
    "没有可运行的步骤或被校验拦下":
        "No executable steps, or validation blocked the run.",
    "当前组": "Current group",
    "定时运行未启用": "Scheduled runs disabled",
    "定时 ": "Scheduled ",
    " 个定时任务 · 最近 ": " tasks · latest ",
    "运行报告已保存：": "Run report saved: ",
    "运行报告保存失败：": "Failed to save run report: ",
    "结果已写入：": "Results written to: ",
    "结果写入失败：": "Failed to write results: ",
    "运行结束": "Run completed",
    " 步，失败 ": " steps, failed ",
    "，耗时 ": ", duration ",
    "点击打开 MeowGo 查看详情": "Click to open MeowGo for details",
    "MeowGo 运行结束：": "MeowGo run completed: ",
    "结束通知已发送": "Completion notification sent",
    "结束通知发送失败：": "Failed to send completion notification: ",
    "导出运行日志": "Export run log",
    "文本": "Text",
    "日志已导出：": "Log exported: ",
    "检测到多显示器：图片识别请在同一屏幕内完成，跨屏（尤其缩放不同时）可能识别不到":
        "Multiple monitors detected. Capture and match images on the same screen; "
        "cross-screen matching (especially with different scaling) may fail.",
    "继续": "Resume",
    "正在执行：": "Executing: ",
    " · 第 ": " · step ",
    "已定位：": "Go to: ",
    "这次运行没有失败的步骤": "This run has no failed steps.",
    "全局停止热键 F9 注册失败（可能被占用）":
        "Failed to register the global stop hotkey F9 (it may be in use).",
    "全局运行热键 F5 注册失败（可能被占用）":
        "Failed to register the global run hotkey F5 (it may be in use).",
    "图片目录：": "Image folder: ",
    "定时任务已保存（": "Scheduled tasks saved (",
    "宏录制": "Macro recording",
    "宏录制完成：已插入 ": "Macro recording completed: inserted ",
    " 个坐标步骤（可用拖拽调整顺序）":
        " coordinate steps (drag to reorder).",
    "关于": "About",
    "确认退出": "Confirm exit",
    "任务仍在运行，确定退出？": "A run is still in progress. Exit anyway?",
    "显示主窗口": "Show main window",
    "托盘图标启动失败，窗口会保持可见":
        "Failed to start the tray icon; the window will remain visible.",
    "托盘图标已开启": "Tray icon enabled",
    "托盘图标已关闭": "Tray icon disabled",
    "MeowGo 还在运行": "MeowGo is still running",
    "窗口已收起；双击托盘图标重新打开，右键菜单可退出。":
        "The window has been hidden. Double-click the tray icon to reopen it; "
        "use its context menu to exit.",
    "窗口已最小化到托盘": "Window minimized to the system tray",
    "MeowGo 已启动": "MeowGo started",
    "窗口已按设置隐藏到托盘，双击图标打开界面。":
        "The window was hidden to the system tray as configured. "
        "Double-click the icon to open it.",
    "已按设置启动并隐藏到托盘": "Started and hidden to the system tray as configured",
    "按键录入只支持 Windows": "Key capture is only supported on Windows.",
    "录制中：直接按组合键，全部松开自动录入；单独按 Esc 取消":
        "Recording: press the key combination and release all keys to capture it. "
        "Press Esc alone to cancel.",
    "⚠ 引擎不识别顶层数字键（旧版限制），请改按小键盘数字 num0~num9":
        "⚠ The engine does not recognize top-row number keys (legacy limitation). "
        "Use the numeric keypad keys num0–num9 instead.",
    "宏录制只支持 Windows": "Macro recording is only supported on Windows.",
    "录制中：正常操作鼠标，点本窗口的「停止」结束":
        "Recording: use the mouse normally, then click Stop in this window to finish.",
    "点「开始录制」后正常操作鼠标；\n录的是绝对坐标，适合界面固定的场景。\n停止时请用本窗口的按钮。":
        "Click Start recording and use the mouse normally.\n"
        "Absolute coordinates are recorded, which suits fixed layouts.\n"
        "Use the Stop button in this window to finish.",
    "未开始": "Not started",
    "开始录制": "Start recording",
    "无法启动鼠标录制（鼠标钩子安装失败）":
        "Cannot start mouse recording (mouse hook installation failed).",
    "停止并生成步骤": "Stop and create steps",
    "已生成 ": "Created ",
    "已取消 / 没有录到操作": "Canceled / no actions recorded",
    "准备开始": "Preparing",
    "取消": "Cancel",

    # 补充：主界面 / 定时任务动态文案
    "双击重命名 · 拖拽排序 · 右键查看更多操作":
        "Double-click to rename · Drag to reorder · Right-click for more",
    "浏览…": "Browse…",
    "（当前组）": "(Current group)",
    "暂无定时任务": "No scheduled tasks",
    "请点击左侧「新增」创建；行内开关可快速启用或停用。":
        "Click Add on the left to create one. Use the row switch to enable or disable it.",
    "修改后请点击底部「保存」；左侧开关可快速启用或停用任务。":
        "Click Save below to apply changes. Use the row switch to enable or disable the task.",
    "请添加第一个步骤，或通过右键菜单批量导入。":
        "Add the first step, or import steps from the right-click menu.",
    "此执行组尚未添加步骤": "This group has no steps yet",
    "暂无已启用的定时任务": "No enabled scheduled tasks",
    "仅保留最近 60 条记录；客户端运行时将按时触发。":
        "Only the last 60 records are kept. Tasks run while the client is open.",
    "左侧维护执行组，右侧编辑步骤；「调用执行组」指令可复用其它组。":
        "Maintain groups on the left and edit steps on the right. "
        "The Call group step reuses other groups.",
    "欢迎使用 MeowGo": "Welcome to MeowGo",
    "未安装 OpenCV（opencv-python），图片识别步骤无法运行。"
    "请在命令行执行：pip install opencv-python":
        "OpenCV (opencv-python) is not installed, so image steps cannot run. "
        "Run this in a command prompt: pip install opencv-python",
    "截图几乎是纯色，可能没框到内容；请确认目标已显示后重新截图":
        "The screenshot is almost a single color, so it may not contain the "
        "target. Make sure the target is visible, then capture it again.",
    "运行日志": "Run log",
    "收起 / 展开日志": "Collapse / expand log",
    "未找到与“": "No steps matching “",
    "”匹配的步骤": "”",

    # 指令类型（Step.label() / 下拉框）
    "左键单击": "Left click",
    "左键双击": "Double-click",
    "右键单击": "Right-click",
    "输入文本": "Input text",
    "等待": "Wait",
    "滚轮": "Scroll",
    "热键组合": "Hotkey",
    "粘贴当前时间": "Paste current time",
    "系统命令": "System command",
    "调用执行组": "Call group",
    "条件：出现图片则执行": "Condition: image found",
    "窗口操作": "Window action",
    "坐标点击": "Click coordinates",
    "移动鼠标": "Move pointer",
    "条件：没出现图片则执行": "Condition: image not found",
    "条件：二选一": "Condition: either/or",
    "条件：窗口存在则执行": "Condition: window exists",
    "设置变量": "Set variable",
    "复制选中文字到变量": "Copy selection to variable",
    "拖拽": "Drag",
    "基础": "Basics",

    # 指令提示
    "图片文件名，如 按钮.png": "Image file name, e.g. button.png",
    "要输入的文字": "Text to enter",
    "等待秒数，如 1.5": "Seconds to wait, e.g. 1.5",
    "滚动距离，正数向上 / 负数向下，如 -200":
        "Scroll distance: positive = up, negative = down, e.g. -200",
    "用英文逗号分隔，如 ctrl,s 或 win,r":
        "Separate keys with commas, e.g. ctrl,s or win,r",
    "固定填写 当前时间（保留该关键字）":
        "Enter the fixed keyword 当前时间 (keep this keyword).",
    "CMD 命令，如 start notepad": "CMD command, e.g. start notepad",
    "要调用的组名（实现步骤复用）": "Group to call (reuses its steps)",
    "图片名|组名，如 弹窗.png|关闭弹窗":
        "Image name|group name, e.g. dialog.png|Close dialog",
    "activate:标题 / wait:标题|秒 / close:标题":
        "activate:title / wait:title|seconds / close:title",
    "x,y，如 500,300；可加 ,right 表示右键":
        "x,y, e.g. 500,300; add ,right for a right click",
    "x,y，如 500,300": "x,y, e.g. 500,300",
    "图片名|组名": "Image name|group name",
    "图片名|出现执行|没出现执行": "Image|group if found|group if not found",
    "窗口标题|组名": "Window title|group name",
    "变量名=值，如 次数={index}": "Name=value, e.g. count={index}",
    "变量名，如 订单号": "Variable name, e.g. order_id",
    "起点图|终点图；也可写 x1,y1|x2,y2":
        "Start image|end image; or x1,y1|x2,y2",
    "重复次数：": "Repeat count: ",
    "点击次数": "click count",
    "双击次数": "double-click count",
    "（-1 表示无限）": "(-1 = unlimited)",
    "执行次数": "run count",
    "填写企业微信或钉钉机器人地址，任务结束后自动推送；留空则不推送。完成通知由所有定时任务共用。":
        "Enter a WeCom or DingTalk bot URL to send a notification after each run; "
        "leave blank to disable. Completion notifications are shared by all tasks.",
    "完成通知": "Completion notification",
    "展开 / 收起指令类型": "Expand / collapse step types",
    "展开 / 收起选项": "Show / hide options",
    "没有匹配的指令类型": "No matching step types",
    "搜索过滤中不能拖动排序，请先清空搜索框":
        "Cannot reorder while a search filter is active. Clear the search first.",
    "无法启动键盘录制（键盘钩子安装失败），请手动输入":
        "Cannot start keyboard recording (keyboard hook installation failed). "
        "Enter the keys manually.",
    "已取消按键录入": "Key capture canceled.",
    "没有可撤销的操作": "Nothing to undo.",
    "没有可重做的操作": "Nothing to redo.",
    "框选区域太小，已取消": "Selected region is too small; canceled.",
    "日志为空，没有可复制的内容": "The log is empty; nothing to copy.",
    "正在停止…": "Stopping…",
    "已复制步骤": "Step duplicated.",
    "运行中…": "Running…",
    "运行中…（循环）": "Running… (loop)",
    "已暂停": "Paused",
    "这次运行没有失败的步骤": "This run has no failed steps.",
    "执行完成": "Completed",
    "执行出错": "Failed",
    "也可点击右侧键盘按钮录入组合键（如 Ctrl+Shift+A）。":
        "You can also use the keyboard button on the right to capture a combination "
        "(e.g. Ctrl+Shift+A).",
    "左侧填写判断图像，右侧填写条件满足后执行的执行组（可点击「选组」）。":
        "Enter the condition image on the left and the group to run on match on the "
        "right (click Choose group).",
    "下方两个输入框分别填写图像出现与未出现时执行的执行组（均可点击「选组」）。":
        "Enter the groups to run when the image is found and not found in the two "
        "fields below (both support Choose group).",
    "设置后，任意步骤的内容均可通过 {变量名} 读取。":
        "After setting, any step can read the value with {variable name}.",
    "请先复制目标文字；此步骤会将其存入指定变量（发送一次 Ctrl+C，随后恢复剪贴板内容）。":
        "Copy the target text first. This step stores it in the specified variable "
        "(it sends Ctrl+C once and restores the clipboard afterwards).",
    "截图、选图或拾取的结果将填入第一个空白段。":
        "The result of Capture, Choose image, or Pick fills the first empty segment.",
    "窗口标题可只填写其中一部分，例如 activate:记事本。":
        "You can use part of the window title, e.g. activate:Notepad.",
    "也可将鼠标移至目标位置，点击「拾取」自动填入坐标。":
        "You can also move the pointer to the target and click Pick to fill the coordinates.",
    "默认通过剪贴板粘贴（支持中文）。":
        "Pastes through the clipboard by default (supports Chinese).",
    "若个别应用不支持粘贴，可在「高级选项」中改为逐字输入。":
        "If an application does not accept paste, switch to character-by-character "
        "input under Advanced options.",
    "截图或选图后，请点击「预览」核对内容。":
        "After capturing or choosing an image, click Preview to verify it.",
    "若定位有误，可在「高级选项」中框选识别范围或指定第几个匹配项。":
        "If it targets the wrong element, set a region or occurrence under Advanced options.",
    "可在「高级选项」中设置最长等待时间。":
        "Set the maximum wait time under Advanced options.",
    "天 ": "d ",
    "今天": "Today",
    "明天": "Tomorrow",
    "周一": "Mon",
    "周二": "Tue",
    "周三": "Wed",
    "周四": "Thu",
    "周五": "Fri",
    "周六": "Sat",
    "周日": "Sun",
    "调用组：": "Call group: ",
    "图片文件名，如 菜单.png": "Image file name, e.g. menu.png",
    "知道了": "Got it",
    "恢复选中": "Restore selected",
    "【概述】": "Overview",
    "左侧管理执行组，右侧编辑组内步骤，点击「运行组」即可执行。":
        "Manage groups on the left and edit their steps on the right. "
        "Click Run group to execute.",
    "所有执行组均自动保存，无需手动维护项目文件。":
        "All groups are saved automatically; no project file needs to be managed.",
    "执行组可通过「调用执行组」指令被其他执行组复用。":
        "Groups can be reused by other groups through the Call group step.",
    "【操作要点】": "Key operations",
    "· 执行组操作：在左侧执行组上右键，可新建、重命名、复制、删除或排序。":
        "· Group actions: right-click a group on the left to create, rename, "
        "duplicate, delete, or reorder it.",
    "· 运行指定执行组：在该执行组上右键，选择「运行这个组」。":
        "· Run a specific group: right-click it and choose Run this group.",
    "· 步骤列表：双击单元格可编辑内容、类型、重复次数与备注。":
        "· Step list: double-click a cell to edit its content, type, repeat count, "
        "or note.",
    "· 右键菜单提供新增、编辑、复制、删除、截图及上下移动等完整操作。":
        "· The right-click menu provides add, edit, duplicate, delete, capture, and "
        "move actions.",
    "· 图像类步骤可点击「截图」框选屏幕区域，并自动保存至图像目录。":
        "· Image steps support Capture to select a screen region and save it to the "
        "image folder automatically.",
    "· 复用编排：在组内新增「调用执行组」，内容填写目标执行组名称。":
        "· Reuse: add a Call group step and enter the target group name.",
    "【指令类型】": "Step types",
    "【快捷键】": "Shortcuts",
    "Ctrl+G 新建执行组　Ctrl+Enter 新增步骤　F2 编辑步骤　Delete 删除步骤":
        "Ctrl+G New group   Ctrl+Enter Add step   F2 Edit step   Delete Delete step",
    "F5 运行当前组（全局）　F9 停止（全局，任何时候均有效）":
        "F5 Run current group (global)   F9 Stop (global, always available)",
    "【安全】将鼠标快速移至屏幕左上角，可立即中断执行。":
        "Safety: quickly move the pointer to the top-left corner of the screen to "
        "abort execution.",
    "MeowGo\n\n将原 Excel 配置迁移至客户端界面：可视化维护执行组与步骤，\n支持执行组间调用复用、屏幕截图、运行前校验、日志记录与全局停止热键。\n\n执行组自动保存于程序目录的 rpa_settings.json；\n支持 Excel 单组或全部工作表导入导出（兼容旧版 cmd.xls）。":
        "MeowGo\n\n"
        "Migrate existing Excel configurations into the client: maintain groups and "
        "steps visually, reuse groups through calls, capture screenshots, validate "
        "before running, and review logs with a global stop hotkey.\n\n"
        "Groups are saved automatically to rpa_settings.json in the program folder. "
        "Excel import and export support a single group or all worksheets (legacy "
        "cmd.xls files are also supported).",
}


# ---------------------------------------------------------------------------
# 动态文案
# ---------------------------------------------------------------------------
# 带数量、路径、组名的句子无法整句命中，按固定前后缀替换；顺序从长到短。
_PATTERNS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = tuple(
    (re.compile(pattern), repl) for pattern, repl in (        (r"^已保存 · ", "Saved · "),
        (r"^未保存…$", "Unsaved…"),
        (r"^已切换到深色主题$", "Switched to dark theme"),
        (r"^已切换到浅色主题$", "Switched to light theme"),
        (r"^已新建执行组：(.+)$", r"Group created: \1"),
        (r"^已删除执行组：(.+)$", r"Group deleted: \1"),
        (r"^已复制为执行组：(.+)$", r"Group duplicated: \1"),
        (r"^已重命名为 (.+)$", r"Renamed to \1"),
        (r"^已撤销：(.+)$", r"Undone: \1"),
        (r"^已重做：(.+)$", r"Redone: \1"),
        (r"^已导入 (\d+) 个执行组：(.+)$", r"Imported \1 groups: \2"),
        (r"^已导出 (\d+) 个执行组到 (.+)$", r"Exported \1 groups to \2"),
        (r"^已导出到 (.+)$", r"Exported to \1"),
        (r"^已恢复配置备份：(.+)$", r"Configuration backup restored: \1"),
        (r"^已恢复 (\d+) 个预置执行组$", r"Restored \1 preset groups"),
        (r"^已复制 (\d+) 个步骤$", r"Duplicated \1 steps"),
        (r"^已删除 (\d+) 步$", r"Deleted \1 steps"),
        (r"^已移动 (\d+) 步$", r"Moved \1 steps"),
        (r"^已更新第 (\d+) 步$", r"Updated step \1"),
        (r"^已录入热键：(.+)$", r"Hotkey captured: \1"),
        (r"^已录入：(.+)$", r"Captured: \1"),
        (r"^已生成 (\d+) 个步骤$", r"Created \1 steps"),
        (r"^已清理 (\d+) 张未引用图片$", r"Cleaned \1 unreferenced images"),
        (r"^已保存截图：(.+)$", r"Screenshot saved: \1"),
        (r"^日志已导出：(.+)$", r"Log exported: \1"),
        (r"^图片目录：(.+)$", r"Image folder: \1"),
        (r"^定时任务已保存（(.+)）$", r"Scheduled tasks saved (\1)"),
        (r"^运行报告已保存：(.+)$", r"Run report saved: \1"),
        (r"^结果已写入：(.+)$", r"Results written to: \1"),
        (r"^(\d+) 行日志到剪贴板$", r"\1 log lines copied to clipboard"),
        (r"^(\d+) 个组$", r"\1 groups"),
        (r"^(\d+) 张$", r"\1 images"),
        (r"^(\d+) 个任务$", r"\1 tasks"),
        (r"^(\d+) 步 · 失败 (\d+)$", r"\1 steps · failed \2"),
        (r"^还有 (\d+) 个任务…$", r"And \1 more tasks…"),
        (r"^近 (\d+) 次 · 最近 (.+)$", r"Last \1 records · latest \2"),
        (r"^第 (\d+) 个$", r"Occurrence \1"),
        (r"^第 (\d+) 步$", r"Step \1"),
        (r"^第 (\d+) 个任务$", r"Task \1"),
        (r"^找到 (\d+) 处匹配，可能定位不准（可使用「第几个」或框选识别范围）$",
         r"\1 matches found; positioning may be inaccurate. Use Occurrence or "
         r"select a matching region."),
        (r"^仅找到 (\d+) 处匹配，不存在第 (\d+) 个$",
         r"Only \1 matches found; occurrence \2 does not exist."),
        (r"^当前组：(.+)（(\d+) 步）$", r"Current group: \1 (\2 steps)"),
        (r"^步骤 · (.+)$", r"Steps · \1"),
        (r"^未找到与“(.+)”匹配的步骤$", r"No steps matching “\1”"),
        (r"^界面语言已切换为 (.+)$", r"Interface language changed to \1"),
        (r"^开机自启通过当前用户注册表 Run 项实现.*启动命令：(.+)$",
         r"Startup is registered in the current user's Run key and does not require "
         r"administrator rights. Signing in only opens the client; it does not run a "
         r"workflow automatically. For unattended scheduled runs, use Scheduled runs "
         r"→ Create scheduled task. Launch command: \1"),
        (r"^已删除 (\d+) 个步骤$", r"Deleted \1 steps"),
        (r"^已移动 (\d+) 个步骤$", r"Moved \1 steps"),
        (r"^已复制 (\d+) 个步骤$", r"Duplicated \1 steps"),
        (r"^已录入热键：(.+)（回车确认）$", r"Hotkey captured: \1 (press Enter to confirm)"),
        (r"^已复制 (\d+) 行日志到剪贴板$", r"\1 log lines copied to clipboard"),
        (r"^单步调试开$", r"Step debug: On"),
        (r"^单步调试关$", r"Step debug: Off"),
        (r"^正在执行：(.+) · 第 (\d+) 步$", r"Executing: \1 · step \2"),
        (r"^已定位：(.+) · 第 (\d+) 步$", r"Go to: \1 · step \2"),
        (r"^已重命名为 (.+)，同步 (\d+) 处引用$",
         r"Renamed to \1; \2 references updated"),
        (r"^(\d+) 步 · 失败 (\d+) · (.+) 秒$",
         r"\1 steps · failed \2 · \3 s"),
        (r"^(\d{2}:\d{2}) · 当前组$", r"\1 · Current group"),
        (r"^上次在第 (\d+) 行失败，是否从第 (\d+) 行继续重试？\n\n选择「否」将从第一行开始。$",
         r"Row \1 failed last time. Retry from row \2?\n\n"
         r"Choose No to start from the first row."),
        (r"^上次已执行至第 (\d+) 行，是否从第 (\d+) 行继续？\n\n选择「否」将从第一行开始。$",
         r"Last run completed row \1. Continue from row \2?\n\n"
         r"Choose No to start from the first row."),
        (r"^上次已完成全部 (\d+) 行，是否重新从头执行？$",
         r"All \1 rows were completed last time. Run again from the beginning?"),
        (r"^文件：(.+)$", r"File: \1"),
        (r"^共 (\d+) 行数据 · (\d+) 列：(.+)$",
         lambda m: f"{m.group(1)} rows · {m.group(2)} columns: "
                   f"{m.group(3).replace('、', ', ')}"),
        (r"^共 (\d+) 张图片，(\d+) 张未被引用（目录为空或不存在：(.+)）$",
         r"\1 images, \2 unreferenced (folder is empty or missing: \3)"),
        (r"^执行组「(.+)」· 第 (\d+) 步$", r"Group “\1” · step \2"),
        (r"^(\d+) × (\d+) 像素 · 已缩放至 (\d+)% · (.+)$",
         r"\1 × \2 px · scaled to \3% · \4"),
        (r"^(\d+) × (\d+) 像素 · 已缩放至 (\d+)%$",
         r"\1 × \2 px · scaled to \3%"),
        (r"^(\d+) × (\d+) 像素 · (.+)$", r"\1 × \2 px · \3"),
        (r"^(\d+) × (\d+) 像素$", r"\1 × \2 px"),
        (r"^(左键单击|左键双击|右键单击|输入文本|等待|滚轮|热键组合|粘贴当前时间|"
         r"系统命令|调用执行组|条件：出现图片则执行|窗口操作|坐标点击|移动鼠标|"
         r"条件：没出现图片则执行|条件：二选一|条件：窗口存在则执行|设置变量|"
         r"复制选中文字到变量|拖拽)：(.+)$",
         lambda m: f"{tr(m.group(1))}: {m.group(2)}"),
    )
)


_PREFIXES: tuple[tuple[str, str], ...] = (
    ("重复次数：", "Repeat count: "),
    ("可调用：", "Available groups: "),
)


def set_language(language: str) -> str:
    """设置当前语言，返回实际生效的语言代码。"""
    _state["language"] = language if language in dict(LANGUAGES) else DEFAULT_LANGUAGE
    return _state["language"]


def get_language() -> str:
    return _state["language"]


def language_label(language: str | None = None) -> str:
    return dict(LANGUAGES).get(language or get_language(), "简体中文")


def language_display(language: str | None = None) -> str:
    """下拉框里显示的语言名：中文界面用本名，英文界面用英文名。"""
    code = language or get_language()
    if get_language() == "en":
        return LANGUAGE_ENGLISH_NAMES.get(code, "Chinese (Simplified)")
    return dict(LANGUAGES).get(code, "简体中文")


def language_code(label: str) -> str:
    for code, name in LANGUAGES:
        if label in (name, LANGUAGE_ENGLISH_NAMES.get(code)):
            return code
    return DEFAULT_LANGUAGE


def tr(text: Any) -> Any:
    """翻译一段源文案；非字符串或没有译文时原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    if _state["language"] == "zh":
        return text
    hit = EN.get(text)
    if hit is not None:
        return hit
    for pattern, repl in _PATTERNS:
        if pattern.search(text):
            return pattern.sub(repl, text)
    # 多行提示（类型说明 + 补充说明）按行翻译，避免整句无法命中
    if "\n" in text:
        lines = [tr(line) for line in text.split("\n")]
        return "\n".join(lines)
    # 「重复次数：点击次数」这类“前缀 + 可翻译片段”
    for prefix, english in _PREFIXES:
        if text.startswith(prefix):
            return english + str(tr(text[len(prefix):]))
    return text


def set_text_source(widget, source: str, **options) -> None:
    """给已安装翻译层的控件设置“源文案”，按当前语言显示。

    业务代码只需传中文源文案；这里负责保存源文案（用于再次切换语言），
    同时避免 configure 把译文误存成新的源文案。
    """
    global _applying
    previous = _applying
    _applying = True
    try:
        if hasattr(widget, "set_text"):
            try:
                widget._mg_i18n_source = source
            except Exception:
                pass
            widget.set_text(source)
        else:
            try:
                setattr(widget, _source_attr, source)
            except Exception:
                pass
            widget.configure(text=tr(source), **options)
    finally:
        _applying = previous


def variants(text: str) -> set[str]:
    """返回同一段源文案在支持语言里的所有写法（用于占位符判断）。"""
    result = {text}
    english = EN.get(text)
    if english:
        result.add(english)
    return result


# 紧凑别名，业务代码里读起来更轻
_ = tr


# ---------------------------------------------------------------------------
# Tk 控件拦截
# ---------------------------------------------------------------------------

def _text_from_args(args: tuple, kwargs: dict) -> tuple[Any, bool]:
    """从 Tk 构造参数里取出 text，并返回 (源文案, 是否来自 cnf 字典)。"""
    if "text" in kwargs:
        return kwargs.get("text"), False
    if len(args) >= 2 and isinstance(args[1], dict) and "text" in args[1]:
        return args[1].get("text"), True
    return None, False


def _replace_text_arg(args: tuple, kwargs: dict, value: str, from_cnf: bool):
    if from_cnf:
        cnf = dict(args[1])
        cnf["text"] = value
        return (args[0], cnf) + args[2:], kwargs
    kwargs = dict(kwargs)
    kwargs["text"] = value
    return args, kwargs


def _patch_text_widget(cls) -> None:
    if getattr(cls, "_mg_i18n_patched", False):
        return
    original_init = cls.__init__
    original_configure = cls.configure

    def init(self, *args, **kwargs):
        source, from_cnf = _text_from_args(args, kwargs)
        if isinstance(source, str):
            args, kwargs = _replace_text_arg(
                args, kwargs, tr(source), from_cnf)
        original_init(self, *args, **kwargs)
        if isinstance(source, str):
            setattr(self, _source_attr, source)

    def configure(self, cnf=None, **kwargs):
        source = None
        if isinstance(cnf, dict) and isinstance(cnf.get("text"), str):
            source = cnf["text"]
            cnf = dict(cnf)
            cnf["text"] = tr(source)
        if isinstance(kwargs.get("text"), str):
            source = kwargs["text"]
            kwargs = dict(kwargs)
            kwargs["text"] = tr(source)
        if source is not None and not _applying:
            setattr(self, _source_attr, source)
        if cnf is None:
            return original_configure(self, **kwargs)
        return original_configure(self, cnf, **kwargs)

    cls.__init__ = init
    cls.configure = configure
    cls.config = configure
    cls._mg_i18n_patched = True


def _patch_menu(cls) -> None:
    if getattr(cls, "_mg_i18n_patched", False):
        return
    for name in ("add_command", "add_cascade", "add_checkbutton",
                 "add_radiobutton"):
        original = getattr(cls, name)

        def make(original):
            def method(self, cnf=None, **kwargs):
                if isinstance(cnf, dict) and isinstance(cnf.get("label"), str):
                    cnf = dict(cnf)
                    cnf["label"] = tr(cnf["label"])
                if isinstance(kwargs.get("label"), str):
                    kwargs = dict(kwargs)
                    kwargs["label"] = tr(kwargs["label"])
                if cnf is None:
                    return original(self, **kwargs)
                return original(self, cnf, **kwargs)
            return method

        setattr(cls, name, make(original))
    cls._mg_i18n_patched = True


def _patch_treeview() -> None:
    cls = ttk.Treeview
    if getattr(cls, "_mg_i18n_patched", False):
        return
    original_heading = cls.heading
    original_insert = cls.insert
    original_item = cls.item

    def heading(self, column, option=None, **kwargs):
        if isinstance(kwargs.get("text"), str):
            source = kwargs["text"]
            if not _applying:
                sources = getattr(self, "_mg_heading_sources", None)
                if sources is None:
                    sources = {}
                    self._mg_heading_sources = sources
                sources[column] = source
            kwargs["text"] = tr(source)
        if isinstance(option, dict) and isinstance(option.get("text"), str):
            source = option["text"]
            if not _applying:
                sources = getattr(self, "_mg_heading_sources", None)
                if sources is None:
                    sources = {}
                    self._mg_heading_sources = sources
                sources[column] = source
            option = dict(option)
            option["text"] = tr(source)
        if option is None:
            return original_heading(self, column, **kwargs)
        return original_heading(self, column, option, **kwargs)

    def _translate_item_kwargs(kwargs: dict) -> dict:
        if isinstance(kwargs.get("text"), str):
            kwargs = dict(kwargs)
            kwargs["text"] = tr(kwargs["text"])
        values = kwargs.get("values")
        if isinstance(values, (list, tuple)):
            kwargs = dict(kwargs)
            kwargs["values"] = tuple(tr(value) for value in values)
        return kwargs

    def insert(self, parent, index, *args, **kwargs):
        return original_insert(self, parent, index, *args,
                               **_translate_item_kwargs(kwargs))

    def item(self, item, option=None, **kwargs):
        if option is None and kwargs:
            return original_item(self, item,
                                 **_translate_item_kwargs(kwargs))
        if isinstance(option, dict):
            return original_item(self, item,
                                 **_translate_item_kwargs(option))
        return original_item(self, item, option, **kwargs)

    cls.heading = heading
    cls.insert = insert
    cls.item = item
    cls._mg_i18n_patched = True


def _patch_title() -> None:
    if getattr(tk.Wm, "_mg_i18n_patched", False):
        return
    global _original_title
    _original_title = tk.Wm.title

    def title(self, string=None):
        if string is None:
            return _original_title(self)
        source = str(string)
        try:
            setattr(self, "_mg_i18n_title", source)
        except Exception:
            pass
        return _original_title(self, tr(source))

    tk.Wm.title = title
    tk.Wm._mg_i18n_patched = True


def _patch_messagebox() -> None:
    for name in ("showinfo", "showwarning", "showerror",
                 "askyesno", "askokcancel", "askretrycancel", "askquestion"):
        original = getattr(messagebox, name)
        if getattr(original, "_mg_i18n_patched", False):
            continue

        def make(original):
            def wrapper(title=None, message=None, *args, **kwargs):
                if title is not None:
                    title = tr(title)
                if message is not None:
                    message = tr(message)
                return original(title, message, *args, **kwargs)
            wrapper._mg_i18n_patched = True
            return wrapper

        setattr(messagebox, name, make(original))


def _patch_filedialog() -> None:
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        original = getattr(filedialog, name)
        if getattr(original, "_mg_i18n_patched", False):
            continue

        def make(original):
            def wrapper(**kwargs):
                if isinstance(kwargs.get("title"), str):
                    kwargs["title"] = tr(kwargs["title"])
                filetypes = kwargs.get("filetypes")
                if filetypes:
                    translated = []
                    for item in filetypes:
                        if isinstance(item, tuple) and len(item) == 2:
                            translated.append((tr(item[0]), item[1]))
                        else:
                            translated.append(item)
                    kwargs["filetypes"] = translated
                return original(**kwargs)
            wrapper._mg_i18n_patched = True
            return wrapper

        setattr(filedialog, name, make(original))


def install() -> None:
    """安装一次控件拦截；重复调用安全。"""
    global _installed
    if _installed:
        return
    for cls in (tk.Label, tk.Button, tk.Checkbutton, tk.Radiobutton,
                tk.Menubutton, tk.LabelFrame, ttk.Label, ttk.Button,
                ttk.Checkbutton, ttk.Radiobutton, ttk.LabelFrame):
        _patch_text_widget(cls)
    _patch_menu(tk.Menu)
    _patch_treeview()
    _patch_title()
    _patch_messagebox()
    _patch_filedialog()
    _installed = True


# ---------------------------------------------------------------------------
# 已有控件刷新
# ---------------------------------------------------------------------------

def _walk(widget) -> Iterable[tk.Misc]:
    yield widget
    try:
        children = widget.winfo_children()
    except (tk.TclError, AttributeError):
        return
    for child in children:
        yield from _walk(child)


def refresh(root) -> None:
    """按当前语言刷新 root 下所有已存在的控件。"""
    global _applying
    _applying = True
    try:
        for widget in _walk(root):
            # 窗口标题
            title_source = getattr(widget, "_mg_i18n_title", None)
            if isinstance(title_source, str):
                try:
                    _original_title(widget, tr(title_source))
                except tk.TclError:
                    pass
            # 自绘按钮 / 自绘控件的文字
            source = getattr(widget, _source_attr, None)
            if isinstance(source, str) and hasattr(widget, "set_text"):
                try:
                    widget.set_text(source)
                except Exception:
                    pass
            elif isinstance(source, str) and not hasattr(widget, "_mg_heading_sources"):
                try:
                    widget.configure(text=tr(source))
                except tk.TclError:
                    pass
            # Treeview 表头
            headings = getattr(widget, "_mg_heading_sources", None)
            if isinstance(headings, dict):
                try:
                    for column, heading_source in headings.items():
                        ttk.Treeview.heading(
                            widget, column, text=tr(heading_source))
                except tk.TclError:
                    pass
            # 可随语言切换重新生成选项的下拉框
            refresh_language = getattr(widget, "refresh_language", None)
            if callable(refresh_language):
                try:
                    refresh_language()
                except Exception:
                    pass
            # 自绘控件的 Tooltip
            tip = getattr(widget, "_tip", None)
            if tip is not None and hasattr(tip, "refresh_language"):
                try:
                    tip.refresh_language()
                except Exception:
                    pass
    finally:
        _applying = False
