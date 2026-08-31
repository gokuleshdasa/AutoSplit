@echo off
REM Double-click to run the Splitter from source (no exe rebuild required).
setlocal
set "HERE=%~dp0"
set "PYTHONW=%HERE%.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
    echo Python venv not found at %PYTHONW%
    echo Run:  python -m venv .venv
    echo Then: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" "%PYTHONW%" "%HERE%src\main.py" %*
