@echo off
REM --- Project Tracker launcher ---
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo No virtual environment found.
    echo Please open PowerShell here and run the one-time setup:
    echo     python -m venv venv
    echo     venv\Scripts\activate
    echo     pip install -r requirements.txt
    echo     python init_db.py
    pause
    exit /b 1
)

if not exist "database.db" (
    echo No database found. Running first-time setup...
    call venv\Scripts\activate.bat
    python init_db.py
)

call venv\Scripts\activate.bat
echo.
echo Starting Project Tracker on http://localhost:5000
echo Press Ctrl+C to stop.
echo.
python app.py
pause
