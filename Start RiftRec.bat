@echo off
setlocal enableextensions
title RiftRec Recorder

rem ===================================================================
rem  RiftRec pilot launcher - just double-click this file.
rem  First run: sets up a local Python environment (needs internet).
rem  Every run after that: starts the recorder straight away.
rem ===================================================================

rem Always work from this script's own folder (the RiftRec folder),
rem no matter where it is double-clicked from.
cd /d "%~dp0"

rem --- find a Python launcher on this PC ------------------------------
set "PYLAUNCH="
where py >nul 2>nul && set "PYLAUNCH=py"
if not defined PYLAUNCH ( where python >nul 2>nul && set "PYLAUNCH=python" )
if not defined PYLAUNCH (
    echo.
    echo   Python was not found on this PC.
    echo   Please install Python 3.11 or newer from:
    echo       https://www.python.org/downloads/
    echo   During setup, tick "Add python.exe to PATH", then run this file again.
    echo.
    pause
    exit /b 1
)

set "VENV=.venv"
set "VPY=%VENV%\Scripts\python.exe"

rem --- first-time setup: create the environment + install packages ---
if not exist "%VPY%" (
    echo.
    echo   First-time setup - creating the environment and downloading
    echo   the required packages. This happens only once and needs an
    echo   internet connection. Please wait...
    echo.
    %PYLAUNCH% -m venv "%VENV%"
    if errorlevel 1 goto setup_failed
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements-recorder.txt
    if errorlevel 1 goto setup_failed
    echo.
    echo   Setup complete.
    echo.
)

rem --- launch the recorder -------------------------------------------
"%VPY%" -m riftrec gui
if errorlevel 1 (
    echo.
    echo   The recorder closed with an error. See the messages above.
    pause
)
exit /b 0

:setup_failed
echo.
echo   Setup failed. Check your internet connection and try again.
echo   If it keeps failing, delete the ".venv" folder next to this file
echo   and run it once more.
echo.
pause
exit /b 1
