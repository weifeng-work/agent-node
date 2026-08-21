# =====================================================================
# agent-node 命令行壳（无 Node 版）—— start/stop/status/restart/update
# 由 agent-node.cmd 转发调用；不依赖 npm/Node/系统 Python（用内置 venv）。
# =====================================================================
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("start","stop","status","restart","update","help")]
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

function Start-Node {
    if (Is-Running) { Write-Host "节点已在运行，打开面板…"; Open-Panel; return }
    if (-not (Test-Path $PYW)) {
        Write-Host ("未安装或缺少 venv 运行时: " + $PYW) -ForegroundColor Red
        Write-Host "请先运行一键安装: irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex"
        exit 1
    }
    if (Test-Path $URL) { Remove-Item $URL -Force -ErrorAction SilentlyContinue }
    Write-Host "正在启动节点（后台常驻，请稍候…）"
    Start-Process -FilePath $PYW -ArgumentList @("-m","node.main","--data-dir",$DATA) -WorkingDirectory $APP
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
    default   { Write-Host "用法: agent-node start|stop|status|restart|update" }
}