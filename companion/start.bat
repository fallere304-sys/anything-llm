@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo [setup] 仮想環境を作成します...
  py -3 -m venv .venv || (echo Python 3.10+ が必要です & pause & exit /b 1)
  .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
)
if not exist .env copy .env.example .env >nul
.venv\Scripts\python -m buddy
pause
