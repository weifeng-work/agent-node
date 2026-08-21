# =====================================================================
# agent-node 一键安装引导（irm | iex）
#   irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex
#
# 做什么：
#   1) 防御性检测并补装 Node.js / npm、Python(>=3.10)（缺失时用 winget，缺 winget 则提示）
#   2) npm 全局安装 @weifeng-work/agent-node（其 postinstall 把代码部署到
#      %LOCALAPPDATA%\agent-node\app，依赖装到 ...\venv）
#   3) 桌面创建“agent-node”快捷方式（双击＝启动节点或打开面板）
#   4) 启动节点并自动打开面板
#
# 设计原则：不假设用户装有任何开发工具/具备排障能力——全部自检、自动补装，
# 失败时输出清晰的“把这段日志发给任意 AI 助手”提示。
# 可选开关：-SkipShortcut 不建快捷方式；-SkipStart 装完不自动启动。
# =====================================================================
[CmdletBinding()]
param(
    [switch]$SkipShortcut,
    [switch]$SkipStart
)
$ErrorActionPreference = "Stop"

# ---------- 工具函数 ----------
function Step([int]$n, [string]$msg) {
    Write-Host ""
    Write-Host ("[{0}/5] {1}" -f $n, $msg) -ForegroundColor Cyan
}
function Abort([string]$msg) {
    Write-Host ""
    Write-Host ("[安装失败] " + $msg) -ForegroundColor Red
    Write-Host ("如果你不确定怎么处理，请把上面的全部日志复制发给任意 AI 助手，它会帮你排查。") -ForegroundColor Yellow
    exit 1
}
function Try-AppendPath([string]$p) {
    if ($p -and (Test-Path $p) -and ($env:Path -notlike "*$p*")) {
        $env:Path = "$p;$env:Path"
    }
}
function Refresh-Path {
    # 读取机器/用户级 PATH，补进当前会话（安装完后能在同一会话直接用已装的工具）
    try { Try-AppendPath ([Environment]::GetEnvironmentVariable("Path", "Machine")) } catch {}
    try { Try-AppendPath ([Environment]::GetEnvironmentVariable("Path", "User")) } catch {}
    Try-AppendPath "$env:LOCALAPPDATA\Programs\Nodejs"
    Try-AppendPath "$env:ProgramFiles\nodejs"
    Try-AppendPath "$env:APPDATA\npm"
    Try-AppendPath "$env:LOCALAPPDATA\npm"
}
function Get-NodeVersion {
    return (& node -v 2>$null | Select-Object -First 1)
}
function Get-PythonLauncher {
    # 返回一个真实存在的、版本 >=3.10 的 python 启动器名（python / py / python3）；无则 $null
    foreach ($c in @('python', 'py', 'python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            if ($c -eq 'py') { $v = & py -3 --version 2>$null }
            else             { $v = & $c --version 2>$null }
            if ($v -match '3\.(\d+)') {
                if ([int]$Matches[1] -ge 10) { return $c }
            }
        } catch {}
    }
    return $null
}
function Install-WithWinget([string]$id, [string]$need) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Abort ("安装 {0} 需要用到系统下载器 winget，但本机没有。请到 {1} 下载安装后重跑本脚本。" -f $need, $need)
    }
    Write-Host ("  尝试用 winget 安装 {0}（自动下载，请稍候…）" -f $need)
    try {
        winget install -e --id $id --accept-source-agreements --accept-package-agreements | Out-Null
    } catch {
        Abort ("winget 安装 {0} 失败：{1}" -f $need, $_.Exception.Message)
    }
    Refresh-Path
}

# ---------- 开始 ----------
Write-Host ""
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "  agent-node  去中心化局域网多智能体协作节点 —— 一键安装" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan

# [1/5] Node.js / npm
Step 1 "检查 Node.js 与 npm"
if (-not (Get-NodeVersion)) {
    Write-Host "  未检测到 Node.js，尝试自动安装…"
    Install-WithWinget "OpenJS.NodeJS.LTS" "Node.js LTS"
    if (-not (Get-NodeVersion)) { Abort "Node.js 安装后仍不可用，请手动安装 Node.js LTS（https://nodejs.org）后重跑。" }
    Refresh-Path
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Refresh-Path }
Write-Host ("  Node.js " + (Get-NodeVersion)) -ForegroundColor Green
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Abort "npm 不可用（Node.js 已装但 npm 异常），请重装 Node.js 后重跑。" }
Write-Host ("  npm " + (& npm -v 2>$null | Select-Object -First 1)) -ForegroundColor Green

# [2/5] Python
Step 2 "检查 Python（需要 >= 3.10）"
$py = Get-PythonLauncher
if (-not $py) {
    Write-Host "  未检测到可用的 Python(>=3.10)，尝试用 winget 安装…"
    Install-WithWinget "Python.Python.3.12" "Python 3.12"
    $py = Get-PythonLauncher
    if (-not $py) { Abort "Python 安装后仍不可用，请手动安装 Python 3.10+（https://www.python.org/downloads/）并勾选“Add to PATH”后重跑。" }
}
Write-Host "  Python 可用" -ForegroundColor Green

# [3/5] npm 全局安装 agent-node
Step 3 "安装 agent-node（代码/依赖将部署到 %LOCALAPPDATA%\agent-node）"
try {
    # 新版 npm 可能拦截 postinstall 的 allow-scripts 策略 → 为此包放行，让部署照常（尽力而为）
    npm config set allow-scripts=@weifeng-work/agent-node --location=user 2>$null
} catch {}
Write-Host "  正在下载并安装 @weifeng-work/agent-node（需联网，约 1~3 分钟）…"
try {
    npm install -g @weifeng-work/agent-node
} catch {
    Abort ("npm 安装失败：{0}" -f $_.Exception.Message)
}
Refresh-Path

# 定位 agent-node 启动命令（npm 全局 shim，可能因 PATH 未刷新而未在当前会话可见）
$launcher = $null
foreach ($p in @("$env:APPDATA\npm\agent-node.cmd",
                 "$env:LOCALAPPDATA\npm\agent-node.cmd",
                 "$env:APPDATA\npm\agent-node",
                 "$env:LOCALAPPDATA\npm\agent-node")) {
    if ($p -and (Test-Path $p)) { $launcher = $p; break }
}
if (-not $launcher) { $launcher = (Get-Command agent-node.cmd -ErrorAction SilentlyContinue).Source }
if (-not $launcher) { $launcher = (Get-Command agent-node -ErrorAction SilentlyContinue).Source }
if (-not $launcher) { Abort "未找到 agent-node 启动命令，请确认 npm 安装成功（可手动执行: npm install -g @weifeng-work/agent-node）。" }

# [4/5] 桌面快捷方式
Step 4 "创建桌面快捷方式"
if ($SkipShortcut) {
    Write-Host "  已跳过（-SkipShortcut）" -ForegroundColor Yellow
} else {
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE "Desktop" }
    $lnk = Join-Path $desktop "agent-node.lnk"
    try {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        # 目标：静默调用 agent-node shim，双击＝启动节点或打开面板
        $sc.TargetPath = "powershell.exe"
        $sc.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"& '$launcher'`""
        $sc.WorkingDirectory = Split-Path $launcher
        $sc.Description = "agent-node 节点：双击启动或打开面板"
        $sc.Save()
        Write-Host ("  已创建: " + $lnk) -ForegroundColor Green
    } catch {
        Write-Host ("  创建快捷方式失败：" + $_.Exception.Message) -ForegroundColor Yellow
        Write-Host "  不影响使用，可从命令行执行 agent-node 启动。" -ForegroundColor Yellow
    }
}

# [5/5] 启动节点并打开面板
Step 5 "启动节点并打开面板"
if ($SkipStart) {
    Write-Host "  已跳过（-SkipStart）" -ForegroundColor Yellow
} else {
    Write-Host "  正在启动节点并打开面板（后台常驻，首次可能需要 10~30 秒）…"
    try {
        & $launcher
    } catch {
        Write-Host ("  启动失败：" + $_.Exception.Message) -ForegroundColor Red
        Write-Host "  之后可随时双击桌面“agent-node”或执行 agent-node 启动。" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "  安装完成" -ForegroundColor Green
Write-Host "  数据目录: %LOCALAPPDATA%\agent-node\data" -ForegroundColor Green
Write-Host "  启动命令: agent-node      (或双击桌面快捷方式)" -ForegroundColor Green
Write-Host "  卸载:     npm uninstall -g @weifeng-work/agent-node ; 删除 %LOCALAPPDATA%\agent-node" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green