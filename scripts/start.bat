@echo off
REM ==============================================================================
REM  CodeMate - Windows Launcher Script
REM ==============================================================================

setlocal enabledelayedexpansion
title CodeMate

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
cd /d "%ROOT_DIR%"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

where npm >nul 2>nul
if %errorlevel% equ 0 (
    echo Starting CodeMate (Desktop Mode)...
    call npm start
) else (
    echo Starting CodeMate (Web Mode on http://127.0.0.1:7860)...
    python backend\server.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] IDE terminated with an error.
    pause
)
