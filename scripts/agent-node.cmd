@echo off
rem agent-node 无 Node 启动壳（由 install.ps1 生成到 %LOCALAPPDATA%\agent-node\agent-node.cmd）
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-node.ps1" %*
exit /b %errorlevel%