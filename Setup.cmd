@echo off
cd /d "%~dp0"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Use Start Viewer.cmd to open EndoscopeViewerWIN.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.10 or later with Tkinter, then try again.
pause
exit /b 1
