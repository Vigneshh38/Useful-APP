@echo off
rem Stops the hidden SS Answer app.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ss_answer\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo SS Answer stopped.
timeout /t 2 >nul
