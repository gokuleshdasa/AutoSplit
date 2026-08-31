@echo off
setlocal
set "HERE=%~dp0"
set "PYTHONW=%HERE%.venv\Scripts\pythonw.exe"
start "" "%PYTHONW%" "%HERE%src\main.py" --join-custom %*
