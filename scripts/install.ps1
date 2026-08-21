# =====================================================================
# agent-node 一键安装（无 Node 版）：irm | iex
#   irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex
#
# 不依赖 Node / npm / git。只要求系统有 Python>=3.10（没有则 winget 自动装一次），
# 依赖装进本地 venv，不污染系统。源码来自 GitHub 源码 zip。
# 产物（%LOCALAPPDATA%\agent-node\）：
#   app\       代码
#   venv\      Python 虚拟环境 + 依赖
#   data\      运行时数据（仅缺则建，重装/更新不覆盖）
#   agent-node.ps1 / agent-node.cmd   CLI 命令（并把该目录加入用户 PATH → 全局可用）
# 另建桌面快捷方式 + 自动启动并打开面板。
# 可选：-SkipShortcut 不建快捷方式；-SkipStart 装完不启动。
# =====================================================================
[CmdletBinding()]
param(
    [switch]$SkipShortcut,
    [switch]$SkipStart
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ROOT    = Join-Path $env:LOCALAPPDATA "agent-node"
$APP     = Join-Path $ROOT "app"
$VENV    = Join-Path $ROOT "venv"
$DATA    = Join-Path $ROOT "data"
$SRC_URL = "https://codeload.github.com/weifeng-work/agent-node/zip/refs/heads/main"

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Step([int]$n, [string]$msg) { Write-Host ""; Write-Host ("[{0}/8] {1}" -f $n, $msg) -ForegroundColor Cyan }
function Abort([string]$msg) {
    Write-Host ""; Write-Host ("[安装失败] " + $msg) -ForegroundColor Red
    Write-Host "把上面日志复制发给任意 AI 助手即可帮你排查。" -ForegroundColor Yellow
    exit 1
}
function Get-PythonLauncher {
    foreach ($c in @('python','py','python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            if ($c -eq 'py') { $v = & py -3 --version 2>$null }
            else             { $v = & $c --version 2>$null }
            if ($v -match '3\.(\d+)') { if ([int]$Matches[1] -ge 10) { return $c } }
        } catch {}
    }
    return $null
}
function Refresh-Path {
    try {
        $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $u = [Environment]::GetEnvironmentVariable("Path", "User")
        foreach ($p in @($m, $u)) { if ($p -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" } }
    } catch {}
}
function Extract-Zip([string]$zipPath, [string]$destDir) {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $destDir)
}
# 流式下载：分块读流并输出字节/百分比进度（反之为大文件静默卡住）
function Download-File([string]$uri, [string]$outPath) {
    $req = [System.Net.HttpWebRequest]::Create($uri)
    $req.Timeout = 120000
    $req.Method = 'GET'
    $resp = $req.GetResponse()
    $total = $resp.ContentLength
    $in  = $resp.GetResponseStream()
    $fs  = [System.IO.File]::Create($outPath)
    try {
        $buf = New-Object byte[] 65536
        $got = 0L
        $lastPct = -1
        while (($read = $in.Read($buf, 0, $buf.Length)) -gt 0) {
            $fs.Write($buf, 0, $read)
            $got += $read
            if ($total -gt 0) {
                $pct = [int][math]::Floor($got * 100 / $total)
                if ($pct -ne $lastPct -and ($pct % 5 -eq 0 -or $pct -eq 100)) {
                    Write-Host ("`r  {0} / {1} MB ({2}%)" -f
                        [math]::Round($got / 1MB, 1), [math]::Round($total / 1MB, 1), $pct) -NoNewline
                    $lastPct = $pct
                }
            }
        }
        if ($total -gt 0) { Write-Host "" }
    } finally {
        $fs.Close(); $in.Close(); $resp.Close()
    }
}

# ---------- 1/8 Python ----------
Step 1 "检查 Python（需要 >= 3.10；缺失则 winget 自动装）"
$py = Get-PythonLauncher
if (-not $py) {
    Write-Host "  未检测到 Python(>=3.10)，尝试 winget 安装…"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Abort "本机没有 Python 也没有 winget，请手动装 Python 3.10+（https://www.python.org/downloads/，勾选 Add to PATH）后重跑。"
    }
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements | Out-Null
    Refresh-Path
    $py = Get-PythonLauncher
    if (-not $py) { Abort "Python 安装后仍不可用，请手动装并勾选 Add to PATH 后重跑。" }
}
Write-Host ("  Python 可用: " + $py) -ForegroundColor Green

# ---------- 2/8 下载源码 ----------
Step 2 "下载源码"
foreach ($d in @($ROOT, $APP, $DATA)) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null } }
$tmp = Join-Path $env:TEMP ("an-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $srcZip = Join-Path $tmp "src.zip"
    $srcEx  = Join-Path $tmp "src"
    Write-Host ("  下载: " + $SRC_URL)
    Download-File $SRC_URL $srcZip
    Write-Host "  下载完成，解压源码…"
    New-Item -ItemType Directory -Path $srcEx | Out-Null
    Extract-Zip $srcZip $srcEx
    $sub = Get-ChildItem -Path $srcEx -Directory | Select-Object -First 1
    if (-not $sub) { Abort "源码 zip 内容异常。" }
    # 覆盖式更新代码（app 整体替换；data 不动）
    Get-ChildItem -Path $APP -Force | ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    Get-ChildItem -Path $sub.FullName -Force | Copy-Item -Destination $APP -Recurse -Force

    # ---------- 3/8 venv ----------
    Step 3 "创建/更新 Python 虚拟环境并安装依赖"
    $req = Join-Path $APP "requirements.txt"
    if (-not (Test-Path $req)) { Abort "源码缺 requirements.txt。" }
    $venvPy = Join-Path $VENV "Scripts\python.exe"
    if (-not (Test-Path $venvPy)) { & $py -m venv $VENV }
    if ($LASTEXITCODE -ne 0) { Abort "创建 venv 失败。" }
    Write-Host "  虚拟环境就绪，正在安装依赖（首次需下载，视网速约 1-3 分钟）…" -ForegroundColor Yellow
    & $venvPy -m pip install --upgrade pip --disable-pip-version-check
    & $venvPy -m pip install -r $req --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Abort "安装依赖失败（请检查网络）。" }
    Write-Host "  依赖安装完成。" -ForegroundColor Green

    # ---------- 4/8 data ----------
    Step 4 "确保数据目录（保留已有身份/配置）"
    if (-not (Test-Path $DATA)) { New-Item -ItemType Directory -Path $DATA -Force | Out-Null }
    Write-Host "  数据目录: $DATA（已存在则原样保留）" -ForegroundColor Green

    # ---------- 5/8 CLI 命令 ----------
    Step 5 "安装命令行 agent-node（加入用户 PATH）"
    Copy-Item -Force (Join-Path $APP "scripts\agent-node.ps1") (Join-Path $ROOT "agent-node.ps1")
    $cmdPath = Join-Path $ROOT "agent-node.cmd"
    Set-Content -LiteralPath $cmdPath -Encoding ASCII -Value "@echo off`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0agent-node.ps1`" %*`nexit /b %errorlevel%"
    # 加入用户 PATH（仅当缺失）
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$ROOT*") {
        $new = if ($userPath) { "$ROOT;$userPath" } else { $ROOT }
        [Environment]::SetEnvironmentVariable("Path", $new, "User")
        Write-Host ("  已把 " + $ROOT + " 加入用户 PATH（新开终端即可用 agent-node）") -ForegroundColor Green
    } else { Write-Host "  PATH 已包含该目录" }
    # 尽力放行本地运行（.ps1 默认策略 Restricted 会拦；设 CurrentUser=RemoteSigned 让 agent-node 直接可敲）
    try { if ((Get-ExecutionPolicy -Scope CurrentUser) -eq "Restricted") { Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force } } catch {}

    # ---------- 6/8 桌面快捷方式 ----------
    Step 6 "创建桌面快捷方式"
    if ($SkipShortcut) {
        Write-Host "  已跳过（-SkipShortcut）" -ForegroundColor Yellow
    } else {
        $desktop = [Environment]::GetFolderPath('Desktop')
        if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE "Desktop" }
        $lnk = Join-Path $desktop "agent-node.lnk"
        try {
            $ws = New-Object -ComObject WScript.Shell
            $sc = $ws.CreateShortcut($lnk)
            $sc.TargetPath = $cmdPath
            $sc.WorkingDirectory = $ROOT
            $sc.Description = "agent-node 节点：双击启动或打开面板"
            $sc.Save()
            Write-Host ("  已创建: " + $lnk) -ForegroundColor Green
        } catch { Write-Host ("  创建快捷方式失败：" + $_.Exception.Message) -ForegroundColor Yellow }
    }

    # ---------- 7/8 防火墙（尽力而为，需管理员） ----------
    Step 7 "配置防火墙（尽力而为）"
    try {
        netsh advfirewall firewall add rule name="agent-node discovery UDP" dir=in action=allow protocol=UDP localport=41830,41550,60420,31820,26880 enable=yes 2>$null | Out-Null
        netsh advfirewall firewall add rule name="agent-node peer" dir=in action=allow protocol=TCP program="$venvPy" enable=yes 2>$null | Out-Null
        netsh advfirewall firewall add rule name="agent-node peerw" dir=in action=allow protocol=TCP program="$VENV\Scripts\pythonw.exe" enable=yes 2>$null | Out-Null
    } catch {}

    # ---------- 8/8 启动 ----------
    Step 8 "启动节点并打开面板"
    if ($SkipStart) {
        Write-Host "  已跳过自动启动（-SkipStart）；可运行 agent-node start。" -ForegroundColor Yellow
    } else {
        & $cmdPath start
    }
}
finally {
    try { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue } catch {}
}

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "  安装完成" -ForegroundColor Green
Write-Host "  数据目录: %LOCALAPPDATA%\agent-node\data（重装/更新保留）" -ForegroundColor Green
Write-Host "  启动:     双击桌面 agent-node，或 agent-node start / stop / restart / status" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green