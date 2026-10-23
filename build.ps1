#Requires -Version 3.0
<#
.SYNOPSIS
    MeowGo 一键打包入口（Windows PowerShell 5.1 / PowerShell 7+ 通用）.

.DESCRIPTION
    本脚本是跨平台打包核心 build.py 的 Windows 入口：
    自动寻找合适的 Python 3.9+ 解释器，然后把全部参数原样转交给 build.py。
    真正的打包逻辑（虚拟环境、依赖安装、平台图标转换、PyInstaller）都在
    build.py 中；macOS / Linux 请改用同目录的 build.sh。

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
function Write-Fail([string]$Msg) {
    Write-Host "`n[失败] $Msg" -ForegroundColor Red
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    exit 1
}

# 取一个候选解释器的版本字符串（失败返回 $null）
function Get-PythonVersion([string]$Cmd, [string[]]$Prefix) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # 原生命令写 stderr 时不要抛异常
    try {
        $out = & $Cmd @Prefix --version 2>&1 | Out-String
    } catch {
        $ErrorActionPreference = $prev
        return $null
    }
    $ErrorActionPreference = $prev
    return $out.Trim()
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
foreach ($c in $candidates) {
    $name, $prefix = $c[0], $c[1]
    $ver = Get-PythonVersion $name $prefix
    if ($ver -and $ver -match '(?m)^Python\s+(\d+)\.(\d+)') {
        $major, $minor = [int]$Matches[1], [int]$Matches[2]
        if ($major -gt $MinMajor -or ($major -eq $MinMajor -and $minor -ge $MinMinor)) {
            $PyCmd, $PyPrefix = $name, $prefix
            break
        }
        Write-Note "发现 $name（Python $major.$minor），低于 $MinMajor.$MinMinor，已跳过"
    }
}

if (-not $PyCmd) {
    Write-Fail "未找到 Python $MinMajor.$MinMinor+ 解释器。`n" +
               "请先安装 Python 3.9 或更高版本（https://www.python.org/downloads/ ，`n" +
               "安装时勾选 tcl/tk 与 pip），或确认 python / py 在 PATH 中。"
}

$verText = Get-PythonVersion $PyCmd $PyPrefix
Write-Ok "使用解释器: $PyCmd $(($PyPrefix -join ' '))（$verText）"

# 转发全部参数给 build.py，退出码原样带回
& $PyCmd @PyPrefix $BuildPy @args
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host "`n[失败] build.py 退出码 $code" -ForegroundColor Red
    exit $code
}
Write-Ok "全部完成，产物见 dist\ 目录"
