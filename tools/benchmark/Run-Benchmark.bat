@echo off
setlocal
chcp 65001 >nul
title Featherbox benchmark

rem --- find Featherbox's bundled Python (the installer records where it put the app)
set "FB="
for /f "tokens=2,*" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Featherbox" /v InstallLocation 2^>nul ^| find "InstallLocation"') do set "FB=%%b"
if not defined FB set "FB=%LOCALAPPDATA%\Programs\Featherbox"
set "PY=%FB%\python\python.exe"
if not exist "%PY%" (
  echo Could not find Featherbox's Python at:
  echo    %PY%
  echo Is Featherbox installed on this PC?
  pause
  exit /b 1
)

echo ======================================================================
echo  Featherbox benchmark
echo ======================================================================
echo  Measures text, image, voice and speech-to-text speed with the models
echo  you have installed, and records CPU, RAM and GPU use while they run.
echo.
echo  Before you start:
echo    * plug the laptop in (battery mode makes everything slower)
echo    * close other heavy programs (games, video editors, other AI apps)
echo    * open Featherbox (the test starts it if it isn't running)
echo.
echo  The basic test takes about 5-15 minutes without an NVIDIA graphics card.
echo ----------------------------------------------------------------------
set "ARGS="
choice /c YN /n /m "Also make a complete Studio Short? Adds about 10-40 min. [Y/N] "
if errorlevel 2 goto novshort
set "ARGS=%ARGS% --full"
:novshort
choice /c YN /n /m "Also make one AI video clip? Can take 30+ min without NVIDIA. [Y/N] "
if errorlevel 2 goto novideo
set "ARGS=%ARGS% --video"
:novideo
choice /c YN /n /m "Test every installed image model (not only the one in use)? [Y/N] "
if errorlevel 2 goto go
set "ARGS=%ARGS% --all-image-models"
:go
echo.
"%PY%" "%~dp0featherbox_bench.py" %ARGS% %*
echo.
pause
