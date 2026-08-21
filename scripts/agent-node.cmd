@echo off
rem agent-node 命令入口（CMD 转发到 agent-node.ps1，兼容性最高）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-node.ps1" %*
exit /b %errorlevel%