@echo off
REM KSP-CIP - double-click this file, or run it from cmd.
REM
REM It exists because Windows Explorer cannot open a .sh (it asks which program
REM to use) and will not run a .ps1 on a double-click under the default
REM execution policy. A .bat is the one thing Windows runs without asking.
REM
REM All it does is hand over to cip.py, which is where the actual logic lives.
REM Any arguments are passed straight through:  cip.bat doctor
REM
REM Named cip.bat rather than start.bat on purpose: `start` is a cmd built-in,
REM so a file by that name is shadowed by the built-in when typed without an
REM extension, which is a confusing thing to hand a first-time user.

setlocal
cd /d "%~dp0"

REM `python3` on a stock Windows PATH is the Microsoft Store alias - a stub
REM that prints an advert and exits - so it is deliberately not tried here.
REM `py` is the official launcher and is preferred when both are present.
set "CIP_PY="
where /q py.exe && set "CIP_PY=py -3"
if not defined CIP_PY (
  where /q python.exe && set "CIP_PY=python"
)

if not defined CIP_PY (
  echo.
  echo ERROR: Python was not found.
  echo.
  echo     Install Python 3.11 or newer from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

REM So cip.py's own "run this next" hints name the launcher you actually used.
set "CIP_LAUNCHER=cip.bat"

%CIP_PY% cip.py %*
set "CIP_EXIT=%ERRORLEVEL%"

REM A double-clicked window closes the instant the program ends, taking any
REM error message with it. Pause only on failure, so a normal Ctrl-C shutdown
REM does not leave a window waiting for a keypress.
if not "%CIP_EXIT%"=="0" if not "%CIP_EXIT%"=="130" (
  echo.
  pause
)
exit /b %CIP_EXIT%
