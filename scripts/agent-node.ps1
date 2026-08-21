# =====================================================================
# agent-node 命令行壳（无 Node 版）—— start/stop/status/restart/update
# 由 agent-node.cmd 转发调用；不依赖 npm/Node/系统 Python（用内置 venv）。
# =====================================================================
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("start","stop","status","restart","update","mcp","help")]
    [string]$Action = "start"
)
$ErrorActionPreference = "Stop"

$ROOT   = Join-Path $env:LOCALAPPDATA "agent-node"
$APP    = Join-Path $ROOT "app"
$DATA   = Join-Path $ROOT "data"
$PYW    = Join-Path $ROOT "venv\Scripts\pythonw.exe"
$LOCK   = Join-Path $DATA "node.lock"
$URL    = Join-Path $DATA "panel.url"

function Is-Running {
    if (-not (Test-Path $LOCK)) { return $false }
    try { $pidLine = [int]((Get-Content $LOCK -Raw).Trim()) } catch { return $false }
    if ($pidLine -le 0) { return $false }
    try {
        $out = & tasklist /fi "PID eq $pidLine" /nh 2>$null
        return (($out -join "") -match "$pidLine")
    } catch { return $false }
}
function Get-PanelUrl {
    if (Test-Path $URL) { $u = ((Get-Content $URL -Raw).Trim()); if ($u) { return $u } }
    return "http://127.0.0.1:5177/"
}
function Open-Panel { try { Start-Process (Get-PanelUrl) } catch {} }

# 启动后自检：同一 data 目录出现多个 node.main 进程时，只保留持有锁（真正服务面板）的那个，
# 杀掉其余双胞胎。根治"venv 启动器带出系统 Python 副本"等导致的同名重复进程。
function Remove-DuplicateNode {
    if (-not (Test-Path $LOCK)) { return }
    try { $lockPid = [int]((Get-Content $LOCK -Raw).Trim()) } catch { return }
    if ($lockPid -le 0) { return }
    $target = $DATA.ToLower()
    $procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*node.main*" -and $_.CommandLine.ToLower().Contains($target) }
    foreach ($p in $procs) {
        if ($p.ProcessId -eq $lockPid) { continue }
        Write-Host ("  清除重复节点进程 PID=" + $p.ProcessId + "（保留服务 PID=" + $lockPid + "）") -ForegroundColor Yellow
        try { & taskkill /PID $p.ProcessId /F 2>$null | Out-Null } catch {}
    }
}

function Resolve-NodeLauncher {
    # 优先用「基础 pythonw + venv site-packages」启动（单进程）。
    # 不用 venv\Scripts\pythonw.exe 直启：部分环境它是转发器，会额外带出系统 Python 副本造成双进程。
    try {
        $cfg = Join-Path $ROOT "venv\pyvenv.cfg"
        if (Test-Path $cfg) {
            $home = (Get-Content $cfg | Where-Object { $_ -like "home = *" } | Select-Object -First 1) -replace '^home\s*=\s*', ''
            if ($home) { $p = Join-Path $home "pythonw.exe"; if (Test-Path $p) { return $p } }
        }
    } catch {}
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { $pw = Join-Path (Split-Path $c.Source) "pythonw.exe"; if (Test-Path $pw) { return $pw } }
    return $PYW  # 兜底：venv pythonw
}
function Get-VenvSitePackages {
    $sp = Join-Path $ROOT "venv\Lib\site-packages"
    if (Test-Path $sp) { return $sp }
    return ""
}

function Show-McpConfig {
    $venvPy = Join-Path $ROOT "venv\Scripts\python.exe"
    $panel = Get-PanelUrl
    $cf = ($APP -replace '\\','/')
    $cp = ($venvPy -replace '\\','/')
    Write-Host "把下面 JSON 加到你的 AI 客户端 MCP 配置："
    Write-Host ""
    Write-Host ('{')
    Write-Host ('  "mcpServers": {')
    Write-Host ('    "agent-node": {')
    Write-Host ('      "command": "' + $cp + '",')
    Write-Host ('      "args": ["-m", "mcp.server"],')
    Write-Host ('      "cwd": "' + $cf + '",')
    Write-Host ('      "env": { "AGENT_NODE_PANEL": "' + $panel + '" }')
    Write-Host ('    }')
    Write-Host ('  }')
    Write-Host ('}')
    Write-Host ""
    Write-Host "提示：command 一定是 venv 里的 python（依赖装在那里），不是系统 python。"
}

function Start-Node {
    if (Is-Running) { Write-Host "节点已在运行，打开面板…"; Open-Panel; return }
    if (-not (Test-Path $PYW)) {
        Write-Host ("未安装或缺少 venv 运行时: " + $PYW) -ForegroundColor Red
        Write-Host "请先运行一键安装: irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex"
        exit 1
    }
    if (Test-Path $URL) { Remove-Item $URL -Force -ErrorAction SilentlyContinue }
    $launcher = Resolve-NodeLauncher
    $sp = Get-VenvSitePackages
    $saved = $env:PYTHONPATH
    if ($sp) { $env:PYTHONPATH = if ($saved) { "$sp;$saved" } else { $sp } }
    Write-Host "正在启动节点（后台常驻，请稍候…）"
    Start-Process -FilePath $launcher -ArgumentList @("-m","node.main","--data-dir",$DATA) -WorkingDirectory $APP
    $env:PYTHONPATH = $saved
    # 自检兜底：若仍有同 data 的多余节点进程，保留锁（服务）者
    Start-Sleep -Seconds 3
    Remove-DuplicateNode
    $dl = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $dl) {
        Start-Sleep -Seconds 1
        if (Is-Running -and (Test-Path $URL)) {
            Write-Host ("节点已启动，面板: " + (Get-PanelUrl)) -ForegroundColor Green
            Open-Panel
            return
        }
    }
    Write-Host "启动超时：请查看 %LOCALAPPDATA%\agent-node\data\node.log" -ForegroundColor Red
}
function Stop-Node {
    if (-not (Is-Running)) { Write-Host "节点未在运行。"; return }
    $pidLine = [int]((Get-Content $LOCK -Raw).Trim())
    Write-Host "正在停止节点 (PID $pidLine)…"
    try { & taskkill /PID $pidLine /T /F | Out-Null } catch {}
    Write-Host "已停止。"
}
function Show-Status {
    if (Is-Running) {
        $pidLine = [int]((Get-Content $LOCK -Raw).Trim())
        Write-Host "✓ 运行中  PID=$pidLine  面板=$(Get-PanelUrl)"
    } else { Write-Host "未运行。" }
}
function Update-Node {
    Write-Host "更新：重新拉最新源码与依赖（数据保留）…"
    Write-Host "（在线运行：跳转到本机一键安装脚本）"
    & irm "https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1" | iex
}

switch ($Action) {
    "start"   { Start-Node }
    "stop"    { Stop-Node }
    "status"  { Show-Status }
    "restart" { Stop-Node; Start-Sleep -Seconds 1; Start-Node }
    "update"  { Update-Node }
    "mcp"     { Show-McpConfig }
    default   { Write-Host "用法: agent-node start|stop|status|restart|update|mcp" }
}