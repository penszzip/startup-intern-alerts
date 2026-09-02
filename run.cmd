@echo off
REM Wrapper for Task Scheduler. pythonw.exe runs headless (no console flash).
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"C:\Users\Raj\AppData\Local\Programs\Python\Python313\pythonw.exe" main.py %*
