@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import flask, flask_sock, winpty, ruamel.yaml, dotenv, pathspec, PIL.Image" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" app.py --open %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo Aider Deck could not start. Review the error above.
pause
exit /b 1
