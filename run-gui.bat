@echo off
rem Launch the Rewards Farmer GUI.
rem
rem Finds the project's Poetry environment so the window gets selenium and
rem python-dotenv, then starts it with pythonw so no console window is left
rem behind. Double-click this file, or run it from a terminal.
setlocal
cd /d "%~dp0"

rem Ask Poetry where its environment is. If Poetry is not installed, or no
rem environment has been created yet, this prints nothing and we fall through.
set "VENV="
for /f "delims=" %%i in ('poetry env info -p 2^>nul') do set "VENV=%%i"

if defined VENV if exist "%VENV%\Scripts\pythonw.exe" (
	start "" "%VENV%\Scripts\pythonw.exe" "%~dp0src\gui.py"
	exit /b 0
)

rem Fall back to whatever pythonw is on PATH.
where pythonw >nul 2>nul
if not errorlevel 1 (
	start "" pythonw "%~dp0src\gui.py"
	exit /b 0
)

echo.
echo Rewards Farmer could not find a Python environment to run in.
echo.
echo Open a terminal in this folder and run "poetry install" once,
echo then try this launcher again.
echo.
pause
