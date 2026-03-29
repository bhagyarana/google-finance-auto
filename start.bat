@echo off
title Google Finance Bulk Upload
echo.
echo  =========================================
echo   Google Finance Bulk Upload
echo  =========================================
echo.
echo  Starting server at http://localhost:8000
echo  Press Ctrl+C to stop.
echo.
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
pause
