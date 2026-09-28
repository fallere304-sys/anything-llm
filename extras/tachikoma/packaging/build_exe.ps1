# Windows で Tachikoma.exe (1 つのアプリ) を作る (Python 3.11 と git が入っていること)
#   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .build-venv
.\.build-venv\Scripts\python -m pip install --upgrade pip pyinstaller
.\.build-venv\Scripts\python -m unittest discover -s tests
.\.build-venv\Scripts\python packaging\build_app.py --out build\payload.zip
.\.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --console --name Tachikoma `
  --icon packaging\tachikoma.ico --add-data "build/payload.zip:." packaging\launcher.py
Write-Host "できました: dist\Tachikoma.exe"
