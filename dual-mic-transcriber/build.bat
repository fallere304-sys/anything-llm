@echo off
rem ============================================================
rem  Build DualMicTranscriber.exe (Windows x64)
rem  Requires: Python 3.11 (python.org installer / py launcher)
rem  If no prebuilt llama-cpp-python wheel is found, it is built from
rem  source (needs Visual Studio Build Tools (C++) and CMake).
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=python"
py -3.11 --version >nul 2>&1 && set "PY=py -3.11"
echo Using: %PY%

if not exist .venv (
  %PY% -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip || goto :err

rem When llama.cpp is built from source, do not use CPU instructions specific
rem to the build machine (e.g. AVX-512) so the exe runs on ordinary laptops.
set "CMAKE_ARGS=-DGGML_NATIVE=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=OFF"

python -m pip install --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu -r requirements.txt -r requirements-build.txt || goto :err

pyinstaller --noconfirm --clean --onefile --windowed --name DualMicTranscriber ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all llama_cpp ^
  --collect-all onnxruntime ^
  --collect-all tokenizers ^
  --collect-all av ^
  --hidden-import sounddevice ^
  run.py || goto :err

echo.
echo Build finished: %CD%\dist\DualMicTranscriber.exe
if not defined CI pause
exit /b 0

:err
echo.
echo Build FAILED.
if not defined CI pause
exit /b 1
