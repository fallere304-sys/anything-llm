# Windows で tachikoma.exe を作る (Python 3.11 が入っていること)
#   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .build-venv
.\.build-venv\Scripts\python -m pip install --upgrade pip pyinstaller
.\.build-venv\Scripts\python -m unittest discover -s tests
.\.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --console --name tachikoma `
  --icon packaging\tachikoma.ico `
  --add-data "ui/static:ui/static" --add-data "finetune:finetune" `
  --add-data "config.example.json:." --add-data "config.voice.example.json:." --add-data "README.md:." `
  --paths . packaging\entry.py
Write-Host "できました: dist\tachikoma.exe"
