@echo off
chcp 65001 >nul
title Batch Watermark
set PYTHONIOENCODING=utf-8

set PY=C:\Users\Darwei\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" set PY=python

"%PY%" "%~dp0watermarker.py" --config "%~dp0config.json"

echo.
echo ============ DONE ============
pause
