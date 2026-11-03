@echo off
rem Starts SS Answer in the background with no window (same as at Windows sign-in).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" "%~dp0ss_answer.py"
