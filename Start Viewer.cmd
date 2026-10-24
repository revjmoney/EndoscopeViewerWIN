@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0viewer.py"
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0viewer.py"
) else (
    python "%~dp0viewer.py"
)
if errorlevel 1 pause
