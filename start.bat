@echo off
REM Featherbox launcher: sets up Python + UI on first run, then starts the app.
setlocal
cd /d "%~dp0"

if not exist backend\.venv\Scripts\python.exe (
  echo [Featherbox] First run: creating Python environment...
  py -3.12 -m venv backend\.venv || python -m venv backend\.venv || goto :fail
  backend\.venv\Scripts\python -m pip install --upgrade pip
  backend\.venv\Scripts\python -m pip install -r backend\requirements.txt || goto :fail
)

if not exist frontend\dist\index.html (
  echo [Featherbox] Building the interface...
  pushd frontend
  call npm install || goto :fail
  call npm run build || goto :fail
  popd
)

cd backend
.venv\Scripts\python run.py
goto :eof

:fail
echo [Featherbox] Setup failed. See the messages above.
pause
