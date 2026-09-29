@echo off
setlocal
cd /d "%~dp0"
title FF / G3rb Links Resolver

REM ---------- Check Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.9+ from python.org
    echo         and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)

REM ---------- Create virtual environment (first run only) ----------
if not exist "venv\Scripts\activate.bat" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

REM ---------- Activate ----------
call "venv\Scripts\activate.bat"

REM ---------- Install requirements (first run only) ----------
if not exist "venv\.setup_done" (
    echo [2/3] Installing requirements...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 goto :setup_failed
    pip install "scrapling[fetchers]"
    if errorlevel 1 goto :setup_failed

    echo.
    echo [3/3] Installing browsers. This can take a while, please wait...
    scrapling install
    if errorlevel 1 goto :setup_failed

    echo done> "venv\.setup_done"
    echo.
    echo Setup complete.
)

REM ---------- Open browser after a short delay, then run the app ----------
echo.
echo Starting the app at http://localhost:5000
echo Close this window to stop the app.
start "" cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:5000"

python app.py

echo.
echo The app has stopped.
pause
exit /b 0

:setup_failed
echo.
echo [ERROR] Setup failed. See the messages above.
echo         Fix the problem and run this file again.
pause
exit /b 1
