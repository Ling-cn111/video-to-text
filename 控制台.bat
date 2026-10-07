@echo off
rem 双击启动托盘控制台（绕过 .ps1 文件关联与执行策略限制）
cd /d "%~dp0"
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0控制台.ps1"
