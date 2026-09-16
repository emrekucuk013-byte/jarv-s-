@echo off
REM Windows: double-click this file to start Vyron. Sets everything up on the first run.
REM For voice later:  start.bat --voice
setlocal
cd /d "%~dp0"

set PY=
for %%c in (py python3 python) do (
  if not defined PY (
    %%c -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1 && set PY=%%c
  )
)
if not defined PY (
  echo Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add Python to PATH" in the installer, then run this file again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting things up, about a minute...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
if not exist .venv\installed.txt (
  if "%~1"=="--voice" (pip install -q -e ".[voice]") else (pip install -q -e ".")
  echo ok > .venv\installed.txt
)

if not exist .env copy .env.example .env >nul
findstr /b "ANTHROPIC_API_KEY=sk" .env >nul 2>&1
if errorlevel 1 (
  echo.
  echo Vyron needs your Anthropic API key. Get one at https://console.anthropic.com
  set /p KEY="Paste it here and press Enter: "
  powershell -Command "(Get-Content .env) -replace '^ANTHROPIC_API_KEY=.*', 'ANTHROPIC_API_KEY=%KEY%' | Set-Content .env"
  findstr /b "ANTHROPIC_API_KEY=" .env >nul || echo ANTHROPIC_API_KEY=%KEY%>> .env
  echo Saved to .env, kept only on this computer.
)

if "%~1"=="--voice" (
  call :needkey DEEPGRAM_API_KEY "Deepgram key (https://console.deepgram.com, API Keys)"
  call :needkey ELEVENLABS_API_KEY "ElevenLabs key (https://elevenlabs.io, profile menu, API Keys)"
)

python -m vyron %*
echo.
pause
exit /b

:needkey
findstr /r /b "%~1=." .env >nul 2>&1
if errorlevel 1 (
  echo.
  echo Voice needs your %~2
  set /p KEY="Paste it here and press Enter: "
  findstr /b "%~1=" .env >nul 2>&1
  if errorlevel 1 (
    echo %~1=%KEY%>> .env
  ) else (
    powershell -Command "(Get-Content .env) -replace '^%~1=.*', '%~1=%KEY%' | Set-Content .env"
  )
)
exit /b
