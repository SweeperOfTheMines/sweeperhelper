@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The project environment has not been created yet.
    echo Open README.md and follow the installation steps first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "minesweeper_helper.py"
if errorlevel 1 (
    echo.
    echo The helper exited with an error. Copy the message above.
    pause
)
endlocal
