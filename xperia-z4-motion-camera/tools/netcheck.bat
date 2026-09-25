@echo off
rem Z4 MotionCam network check: double-click to run netcheck.ps1 (changes nothing on the PC or router).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0netcheck.ps1"
pause
