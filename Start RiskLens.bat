@echo off
rem RiskLens: double-click this file to start the app and open it in your browser.
rem The first run installs two Python packages and downloads a few stocks.
setlocal
title RiskLens
pushd "%~dp0"

if not exist "risklens\launch.py" (
  echo.
  echo   RiskLens can't start from inside the zip file.
  echo.
  echo   1. Right-click the zip file and choose "Extract All...", then "Extract".
  echo   2. In the folder that opens, double-click "Start RiskLens" again.
  echo.
  pause
  exit /b 1
)

set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python3 -c "import sys" >nul 2>&1 && set "PY=python3"
if not defined PY (
  echo.
  echo   Python is not installed, or Windows can't find it.
  echo.
  echo   1. Install it from https://www.python.org/downloads/ ^(opening it now^).
  echo      In the installer, tick "Add python.exe to PATH".
  echo   2. Then double-click "Start RiskLens" again.
  echo.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

%PY% -m risklens.launch %*
if errorlevel 1 (
  echo.
  echo   RiskLens stopped because of the problem above.
  pause
)
popd
endlocal
