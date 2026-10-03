@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 自分の PC で AI-Buddy.exe を作る場合(通常は GitHub Actions で作成済みの zip を使えば不要)
if not exist .venv (
  py -3 -m venv .venv || (echo Python 3.10+ が必要です & pause & exit /b 1)
)
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller || (pause & exit /b 1)
.venv\Scripts\python packaging\build_exe.py || (pause & exit /b 1)
echo.
echo dist\AI-Buddy\AI-Buddy.exe を作成しました。
pause
