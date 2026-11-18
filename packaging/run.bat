@echo off
setlocal
"%~dp0python\python.exe" -E -s "%~dp0launch.py" %*
exit /b %errorlevel%
