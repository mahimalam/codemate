@echo off
REM ==============================================================================
REM  VexP Code IDE - Windows 1-Click Installer
REM ==============================================================================

setlocal enabledelayedexpansion
title VexP Code IDE - Setup

echo ==================================================
echo   VexP Code IDE - Autonomous AI Coding Harness    
echo ==================================================
echo.

REM 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python 3 is required but not found in PATH.
        echo Please install Python 3.10+ from https://python.org and check "Add python.exe to PATH".
        pause
        exit /b 1
    )
    set PY_CMD=py -3
) else (
    set PY_CMD=python
)

echo [+] Using Python command: !PY_CMD!
!PY_CMD! -c "import sys; print(f'[+] Detected Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"

REM 2. Setup Virtual Environment in .venv
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
cd /d "%ROOT_DIR%"

if not exist ".venv" (
    echo [+] Creating virtual environment in .venv...
    !PY_CMD! -m venv .venv
)

echo [+] Activating virtual environment...
call ".venv\Scripts\activate.bat"

REM 3. Install Python Dependencies
echo [+] Upgrading pip and installing dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q

REM 4. Check Node / NPM for Desktop Mode
echo.
echo [+] Checking Desktop IDE environment...
where npm >nul 2>nul
if %errorlevel% equ 0 (
    echo [+] Installing Electron desktop packages...
    call npm install -q
) else (
    echo     [!] Node/NPM not found. Web mode will work, but install Node.js from https://nodejs.org for Desktop IDE mode.
)

REM 5. Check Ollama (Local AI Engine)
echo.
echo [+] Checking Local Ollama Engine...
where ollama >nul 2>nul
if %errorlevel% equ 0 (
    echo     [✓] Ollama CLI detected.
) else (
    echo     [!] Ollama is not installed on this system.
    echo         For local offline GPU inference, install Ollama from: https://ollama.com
    echo         (You can also use Cloud / Free APIs without Ollama)
)

echo.
echo ==================================================
echo   Installation Successful!                        
echo ==================================================
echo To launch Desktop IDE:
echo     npm start   (or run scripts\start.bat)
echo.
echo To launch in Web Browser:
echo     python backend\server.py
echo     (open http://127.0.0.1:7860)
echo ==================================================
pause
