@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo 使用解释器：%PY%
"%PY%" app.py

if errorlevel 1 (
  echo.
  echo 服务异常退出，按任意键关闭窗口。
  pause >nul
)
