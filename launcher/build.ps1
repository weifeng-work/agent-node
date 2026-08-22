# P2-13: 启动器构建脚本。
# 双斜杠双击即用、零命令行定位：必须用 -H windowsgui 去掉 console 子系统，避免黑窗闪现。
param(
    [string]$Out = "agent-node-launcher.exe"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "go vet: checking..." -ForegroundColor Cyan
go vet ./...

Write-Host "build: $Out (-H windowsgui, strip debug)" -ForegroundColor Cyan
$env:GOOS = "windows"
$env:GOARCH = "amd64"
go build -trimpath -ldflags "-H windowsgui -s -w" -o $Out .

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "OK: $((Resolve-Path $Out).Path)" -ForegroundColor Green