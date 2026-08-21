# =====================================================================
# agent-node 零环境一键安装（irm | iex）
#   irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex
# 不需要 Node / npm / git / 系统 Python —— 自带便携 Python 运行时。
#
# 做什么：
#   1) 下载：源码 zip（main 分支）+ 运行时 zip（GitHub 最新 Release 附件）
#   2) 部署到 %LOCALAPPDATA%\agent-node\app（代码+python+bin）
#   3) 建 data（仅缺则建，绝不覆盖已有 → 升级重装不丢身份/数据）
#   4) 生成无 Node 启动壳 agent-node.cmd/.ps1
#   5) 桌面快捷方式 + 自动启动并打开面板
# 可选：-SkipShortcut 不建快捷方式；-SkipStart 装完不启动；-SkipInstall 仅刷新启动壳。
# =====================================================================
[CmdletBinding()]
param(
    [switch]$SkipShortcut,
    [switch]$SkipStart,
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ROOT    = Join-Path $env:LOCALAPPDATA "agent-node"
$APP     = Join-Path $ROOT "app"
$DATA    = Join-Path $ROOT "data"
$PY      = Join-Path $ROOT "python"
$BIN     = Join-Path $ROOT "bin"
$LAUNCH  = Join-Path $ROOT "agent-node.cmd"
$SRC_URL = "https://codeload.github.com/weifeng-work/agent-node/zip/refs/heads/main"
$RT_API  = "https://api.github.com/repos/weifeng-work/agent-node/releases/latest"
$RT_NAME = "agent-node-runtime-windows-x64.zip"

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Step([int]$n, [string]$msg) { Write-Host ""; Write-Host ("[{0}/6] {1}" -f $n, $msg) -ForegroundColor Cyan }
function Abort([string]$msg) {
    Write-Host ""; Write-Host ("[安装失败] " + $msg) -ForegroundColor Red
    Write-Host "把上面日志复制发给任意 AI 助手即可帮你排查。" -ForegroundColor Yellow
    exit 1
}
function Get-Zip([string]$url, [string]$dest) {
    Write-Host ("  下载: " + $url)
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 120
}
function Extract-Zip([string]$zipPath, [string]$destDir) {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $destDir)
}
function Get-RuntimeUrl {
    $rel = Invoke-RestMethod -Uri $RT_API -UseBasicParsing -TimeoutSec 60
    foreach ($a in $rel.assets) {
        if ($a.name -eq $RT_NAME) { return $a.browser_download_url }
    }
    return $null
}

# ---------- 1/6 准备目录 ----------
Step 1 "准备目录"
foreach ($d in @($ROOT, $APP, $DATA)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
New-Item -ItemType Directory -Path "$env:TEMP" -Force | Out-Null
$tmp = Join-Path $env:TEMP ("an-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null

try {
    if (-not $SkipInstall) {
        # ---------- 2/6 下载源码 ----------
        Step 2 "下载源码"
        $srcZip  = Join-Path $tmp "src.zip"
        $srcEx   = Join-Path $tmp "src"
        Get-Zip $SRC_URL $srcZip
        New-Item -ItemType Directory -Path $srcEx | Out-Null
        Extract-Zip $srcZip $srcEx
        $sub = Get-ChildItem -Path $srcEx -Directory | Select-Object -First 1
        if (-not $sub) { Abort "源码 zip 内容异常。" }
        # 覆盖式更新代码（app 整体替换；data 不动）
        if (Test-Path $APP) { Get-ChildItem -Path $APP -Force | ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue } }
        Get-ChildItem -Path $sub.FullName -Force | Copy-Item -Destination $APP -Recurse -Force

        # ---------- 3/6 下载运行时 ----------
        Step 3 "下载运行时（含内置 Python，较大）"
        $rtUrl = Get-RuntimeUrl
        if (-not $rtUrl) { Abort "GitHub 最新 Release 上没有 $RT_NAME 附件，请先在发布流程构建运行时。" }
        $rtZip = Join-Path $tmp "runtime.zip"
        $rtEx  = Join-Path $tmp "rt"
        Get-Zip $rtUrl $rtZip
        New-Item -ItemType Directory -Path $rtEx | Out-Null
        Extract-Zip $rtZip $rtEx
        if (Test-Path (Join-Path $rtEx "python")) { Remove-Item -Recurse -Force (Join-Path $rtEx "python") }
        if (Test-Path $PY) { Remove-Item -Recurse -Force $PY -ErrorAction SilentlyContinue }
        if (Test-Path (Join-Path $rtEx "bin")) { Move-Item (Join-Path $rtEx "bin") $ROOT -Force -ErrorAction Stop }
        Move-Item (Join-Path $rtEx "python") $ROOT -Force -ErrorAction Stop
        if (-not (Test-Path (Join-Path $PY "pythonw.exe"))) { Abort "运行时 zip 缺少 pythonw.exe。" }
    }

    # ---------- 4/6 确保 data（绝不覆盖） ----------
    Step 4 "确保数据目录（保留已有身份/配置）"
    if (-not (Test-Path $DATA)) { New-Item -ItemType Directory -Path $DATA -Force | Out-Null }
    Write-Host "  数据目录: $DATA（已存在则原样保留）" -ForegroundColor Green

    # ---------- 5/6 生成启动壳 + 桌面快捷方式 ----------
    Step 5 "生成启动壳与快捷方式"
    # 启动壳 agent-node.ps1 从仓库代码中复制
    $srcPs1 = Join-Path $APP "scripts\agent-node.ps1"
    if (-not (Test-Path $srcPs1)) { Abort "仓库代码缺 scripts\agent-node.ps1（请更新源码）。" }
    Copy-Item -Force $srcPs1 (Join-Path $ROOT "agent-node.ps1")
    Set-Content -LiteralPath $LAUNCH -Encoding ASCII -Value @"
@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-node.ps1" %*
exit /b %errorlevel%
"@
    if ($SkipShortcut) {
        Write-Host "  已跳过桌面快捷方式（-SkipShortcut）" -ForegroundColor Yellow
    } else {
        $desktop = [Environment]::GetFolderPath('Desktop')
        if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE "Desktop" }
        $lnk = Join-Path $desktop "agent-node.lnk"
        try {
            $ws = New-Object -ComObject WScript.Shell
            $sc = $ws.CreateShortcut($lnk)
            $sc.TargetPath = $LAUNCH
            $sc.WorkingDirectory = $ROOT
            $sc.Description = "agent-node 节点：双击启动或打开面板"
            $sc.Save()
            Write-Host ("  已创建: " + $lnk) -ForegroundColor Green
        } catch {
            Write-Host ("  创建快捷方式失败：" + $_.Exception.Message) -ForegroundColor Yellow
        }
    }

    # 防火墙放行（尽力而为，需管理员）
    try {
        netsh advfirewall firewall add rule name="agent-node discovery UDP" dir=in action=allow protocol=UDP localport=41830,41550,60420,31820,26880 enable=yes 2>$null | Out-Null
        netsh advfirewall firewall add rule name="agent-node peer" dir=in action=allow protocol=TCP program="$PY\pythonw.exe" enable=yes 2>$null | Out-Null
    } catch {}

    # ---------- 6/6 启动 ----------
    Step 6 "启动节点并打开面板"
    if ($SkipStart) {
        Write-Host "  已跳过自动启动（-SkipStart）；双击桌面快捷方式或运行 agent-node start。" -ForegroundColor Yellow
    } else {
        if (-not (Test-Path $LAUNCH)) { Abort "启动壳缺失：$LAUNCH" }
        & $LAUNCH start
    }
}
finally {
    try { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue } catch {}
}

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "  安装完成" -ForegroundColor Green
Write-Host "  数据目录: %LOCALAPPDATA%\agent-node\data（重装/更新保留）" -ForegroundColor Green
Write-Host "  启动:     双击桌面 agent-node，或 agent-node start / stop / restart / update" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green