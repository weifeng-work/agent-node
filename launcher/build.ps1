# P2-13: 启动器构建脚本。
# 双斜杠双击即用、零命令行定位：必须用 -H windowsgui 去掉 console 子系统，避免黑窗闪现。
# -Version 注入 min_launcher 比较用的 launcherVersion（去掉前导 v）；未传时取最近 tag（git describe）。
param(
    [string]$Out = "agent-node-launcher.exe",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ([string]::IsNullOrEmpty($Version)) {
    $desc = git describe --tags --abbrev=0 2>$null
    if ($LASTEXITCODE -eq 0 -and $desc) { $Version = $desc.TrimStart('v') }
}
if ([string]::IsNullOrEmpty($Version)) { $Version = "dev" }  # 无 tag 时保持 checkMinLauncher 跳过语义

Write-Host "go vet: checking..." -ForegroundColor Cyan
go vet ./...

Write-Host "build: $Out (v$Version, -H windowsgui, strip debug)" -ForegroundColor Cyan
$env:GOOS = "windows"
$env:GOARCH = "amd64"
go build -trimpath -ldflags "-H windowsgui -s -w -X main.launcherVersion=$Version" -o $Out .

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "OK: $((Resolve-Path $Out).Path)" -ForegroundColor Green