@echo off
REM === GC Scraper one-click launcher ===
cd /d "%~dp0"

echo.
echo Checking Python...

where py >nul 2>nul
if %errorlevel% neq 0 (
    echo Could not find "py" launcher. Trying "python"...
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo.
        echo *** ERROR: Python is not installed or not on PATH. ***
        echo Please install Python from https://www.python.org/downloads/windows/
        echo (Check "Add python.exe to PATH" during install.)
        echo.
        pause
        goto :eof
    ) else (
        set PYTHON_EXE=python
    )
) else (
    set PYTHON_EXE=py
)

echo Using %PYTHON_EXE%

echo.
echo Installing required Python packages (this may take a minute the first time)...
%PYTHON_EXE% -m pip install --upgrade pip
%PYTHON_EXE% -m pip install pyodbc selenium webdriver-manager beautifulsoup4 python-dotenv

echo.
echo Starting GameChanger scraper...
%PYTHON_EXE% scrape_gc_schedules.py

echo.
echo Done. Press any key to close this window.
pause >nul
