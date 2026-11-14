@echo off
setlocal

echo ====================================================
echo   Starting Vision Surveillance Web Dashboard & API
echo ====================================================

:: 1. Verify virtual environment
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found in .\venv.
    echo Run: python -m venv venv && pip install -r requirements.txt
    pause
    exit /b 1
)

:: 2. Check configuration
if not exist ".env" (
    echo [*] Creating .env from .env.example...
    copy .env.example .env >nul
)

:: 3. Start FastAPI backend in separate window
echo [*] Starting FastAPI backend on http://127.0.0.1:8000 ...
start "Vision Surveillance Backend" cmd /k ".\venv\Scripts\activate.bat && python -m backend.app"

:: 4. Start Dashboard preview server in separate window
echo [*] Starting Web Dashboard on http://127.0.0.1:5173 ...
start "Vision Surveillance Dashboard" cmd /k "npm --prefix dashboard run preview -- --host 127.0.0.1 --port 5173"

timeout /t 3 >nul
echo.
echo ====================================================
echo Dashboard running at: http://127.0.0.1:5173
echo Backend API at:       http://127.0.0.1:8000
echo ====================================================
echo Opening dashboard in your default browser...
start http://127.0.0.1:5173
