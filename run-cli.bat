@echo off
rem Run Rewards Farmer in a command-line window.
rem
rem Uses the project's Poetry environment, so the run has selenium and
rem python-dotenv available. The window stays open at the end because main.py
rem waits for Enter unless REWARDS_HEADLESS is set.
setlocal
cd /d "%~dp0"

title Rewards Farmer

set "VENV="
for /f "delims=" %%i in ('poetry env info -p 2^>nul') do set "VENV=%%i"

if defined VENV if exist "%VENV%\Scripts\python.exe" (
	"%VENV%\Scripts\python.exe" "%~dp0src\main.py"
	goto :end
)

rem Fall back to whatever python is on PATH.
where python >nul 2>nul
if not errorlevel 1 (
	python "%~dp0src\main.py"
	goto :end
)

echo.
echo Rewards Farmer could not find a Python environment to run in.
echo.
echo Open a terminal in this folder and run "poetry install" once,
echo then try this launcher again.
echo.
pause
exit /b 1

:end
