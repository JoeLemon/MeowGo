#Requires -Version 3.0
<#
.SYNOPSIS
    MeowGo 一键打包入口（Windows PowerShell 5.1 / PowerShell 7+ 通用）.

.DESCRIPTION
    本脚本是跨平台打包核心 build.py 的 Windows 入口：
    自动寻找合适的 Python 3.9+ 解释器（必须自带 tkinter），然后把全部参数
    原样转交给 build.py。真正的打包逻辑（虚拟环境、依赖安装、平台图标转换、
    PyInstaller）都在 build.py 中；macOS / Linux 请改用同目录的 build.sh。

    如果已有的 .venv 是 IDE 内置 Python 创建的（通常缺少 tkinter），脚本会先把
    它备份成 .venv.broken-<时间>，再用找到的完整版 Python 重建，避免装完依赖
    才在「检查运行环境」这一步失败。

.PARAMETER args
    所有参数原样透传给 build.py，例如：
        .\build.ps1 --onefile --verify
        .\build.ps1 --clean --no-venv --skip-deps
        .\build.ps1 --mirror https://pypi.tuna.tsinghua.edu.cn/simple
    不带参数直接执行即按默认配置打包（单文件夹模式）。

.EXAMPLE
    .\build.ps1
    默认打包：自动建虚拟环境、装依赖、转 .ico 图标、PyInstaller 单文件夹。

.NOTES
    首次运行如提示"禁止运行脚本"，执行：
        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
    或临时绕过：
        powershell -ExecutionPolicy Bypass -File .\build.ps1
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildPy   = Join-Path $ScriptDir "build.py"
$MinMajor, $MinMinor = 3, 9

function Write-Step([string]$Msg) { Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Write-Ok([string]$Msg)   { Write-Host "    [OK] $Msg" -ForegroundColor Green }
function Write-Note([string]$Msg) { Write-Host "    [提示] $Msg" -ForegroundColor Yellow }
function Write-Fail([string]$Msg, [int]$Code = 1) {
    Write-Host "`n[失败] $Msg" -ForegroundColor Red
    exit $Code
}

# 执行一小段 Python 探测代码，只返回成功与否（输出全部丢掉）
function Test-PythonCode([string]$Cmd, [string[]]$Prefix, [string]$Code) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # 原生命令写 stderr 时不要抛异常
    try {
        & $Cmd @Prefix -c $Code 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $prev
    }
}

# 取一个候选解释器的版本字符串（失败返回 $null）
function Get-PythonVersion([string]$Cmd, [string[]]$Prefix) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Cmd @Prefix --version 2>&1 | Out-String
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $prev
    }
    return $out.Trim()
}

# 虚拟环境是否可用：Python 版本达标，并且能 import tkinter
function Test-VenvUsable([string]$Path) {
    $py = Join-Path $Path "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { return $false }
    $ver = Get-PythonVersion $py @()
    if (-not ($ver -and $ver -match '(?m)^Python\s+(\d+)\.(\d+)')) {
        return $false
    }
    $major, $minor = [int]$Matches[1], [int]$Matches[2]
    if ($major -lt $MinMajor -or ($major -eq $MinMajor -and $minor -lt $MinMinor)) {
        return $false
    }
    return (Test-PythonCode -Cmd $py -Prefix @() -Code "import tkinter")
}

# 只查看帮助时不碰虚拟环境，也不强制 tkinter
$HelpOnly = $false
foreach ($a in $args) {
    if ([string]$a -eq '-h' -or [string]$a -eq '--help') { $HelpOnly = $true }
}

# 解析 --no-venv / --venv，供下面的虚拟环境自检使用
$NoVenv = $false
$VenvArg = ".venv"
for ($i = 0; $i -lt $args.Count; $i++) {
    $a = [string]$args[$i]
    if ($a -eq '--no-venv') {
        $NoVenv = $true
    } elseif ($a -eq '--venv' -and $i + 1 -lt $args.Count) {
        $VenvArg = [string]$args[$i + 1]
        $i++
    } elseif ($a -like '--venv=*') {
        $VenvArg = $a.Substring(7)
    }
}

Write-Step "MeowGo 跨平台打包 [Windows / $env:PROCESSOR_ARCHITECTURE]"

if (-not (Test-Path -LiteralPath $BuildPy)) {
    Write-Fail "找不到 build.py：$BuildPy`n请确认 build.ps1 与 build.py 位于同一目录。"
}

# 候选解释器：py 启动器 > python > python3
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue)      { $candidates += ,@('py', @('-3')) }
if (Get-Command python -ErrorAction SilentlyContinue)  { $candidates += ,@('python', @()) }
if (Get-Command python3 -ErrorAction SilentlyContinue) { $candidates += ,@('python3', @()) }

$PyCmd = $null
$PyPrefix = @()
$PyVersion = $null
foreach ($c in $candidates) {
    $name, $prefix = $c[0], $c[1]
    $ver = Get-PythonVersion $name $prefix
    if (-not ($ver -and $ver -match '(?m)^Python\s+(\d+)\.(\d+)')) { continue }
    $major, $minor = [int]$Matches[1], [int]$Matches[2]
    if ($major -lt $MinMajor -or ($major -eq $MinMajor -and $minor -lt $MinMinor)) {
        Write-Note "发现 $name（Python $major.$minor），低于 $MinMajor.$MinMinor，已跳过"
        continue
    }
    # IDE 自带的精简 Python 常常没有 tkinter，打出来的包会没有界面
    if (-not $HelpOnly -and
        -not (Test-PythonCode -Cmd $name -Prefix $prefix -Code "import tkinter")) {
        Write-Note "发现 $name（Python $major.$minor），但缺少 tkinter，已跳过"
        continue
    }
    $PyCmd, $PyPrefix, $PyVersion = $name, $prefix, $ver
    break
}

if (-not $PyCmd) {
    $need = "Python $MinMajor.$MinMinor+"
    if (-not $HelpOnly) { $need += "（且自带 tkinter）" }
    Write-Fail ("未找到可用的 $need。`n" +
                "请安装 python.org 完整版（安装时勾选 tcl/tk），或确认 py / python 在 PATH 中。")
}

Write-Ok "使用解释器: $PyCmd $(($PyPrefix -join ' '))（$PyVersion）"

# 自检已有虚拟环境：缺 tkinter / 版本过低 / 没有 python.exe 时备份并重建
if (-not $HelpOnly -and -not $NoVenv) {
    $venvPath = $VenvArg
    if (-not [IO.Path]::IsPathRooted($venvPath)) {
        $venvPath = Join-Path $ScriptDir $venvPath
    }
    if (Test-Path -LiteralPath $venvPath) {
        if (-not (Test-Path -LiteralPath (Join-Path $venvPath "pyvenv.cfg"))) {
            Write-Fail ("目标路径已存在，但不是虚拟环境：$venvPath`n" +
                        "请换一个 --venv 目录，或先手动处理该目录。")
        }
        if (Test-VenvUsable $venvPath) {
            Write-Ok "复用虚拟环境: $venvPath"
        } else {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $backup = "$venvPath.broken-$stamp"
            Write-Note "现有虚拟环境不可用（缺少 tkinter / Python 版本过低），备份到：$backup"
            try {
                Move-Item -LiteralPath $venvPath -Destination $backup -ErrorAction Stop
            } catch {
                Write-Fail ("备份旧虚拟环境失败：$($_.Exception.Message)`n" +
                            "请关闭正在占用该目录的程序后重试。")
            }
            & $PyCmd @PyPrefix -m venv $venvPath
            if ($LASTEXITCODE -ne 0) {
                # 创建失败时尽量把旧环境放回去
                try { Move-Item -LiteralPath $backup -Destination $venvPath -ErrorAction Stop } catch {}
                Write-Fail ("重建虚拟环境失败（退出码 $LASTEXITCODE）。`n" +
                            "请确认 $PyCmd 带有 venv 模块，或手动删除 $venvPath 后重试。")
            }
            if (-not (Test-VenvUsable $venvPath)) {
                Write-Fail ("重建后的虚拟环境仍不可用（tkinter 缺失）。`n" +
                            "请换一个自带 tkinter 的 Python，或改用 --no-venv。")
            }
            Write-Ok "已重建虚拟环境: $venvPath"
        }
    }
}

# 转发全部参数给 build.py，退出码原样带回；切到项目根目录，
# 保证 --venv / --icon / --distpath 等相对路径都按项目根解析
Push-Location -LiteralPath $ScriptDir
try {
    & $PyCmd @PyPrefix $BuildPy @args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Write-Host "`n[失败] build.py 退出码 $code" -ForegroundColor Red
    exit $code
}
Write-Ok "全部完成，产物见 dist\ 目录"
