@echo off
REM Robust venv setup for Windows. Tries several fallbacks.
echo Creating virtual environment in .venv

REM Remove existing broken venv if present
if exist .venv (
  echo Removing existing .venv folder (if broken)...
  rmdir /s /q .venv
)

REM Try using the py launcher first
py -3 -m venv .venv 2>nul && goto created

REM Fallback: try `python` on PATH
python -m venv .venv 2>nul && goto created

REM Fallback: try virtualenv (install if needed)
echo Trying virtualenv as a fallback...
python -m pip install --user virtualenv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  echo Could not install virtualenv. You may need to install pip or run as Administrator.
  goto fail
)
python -m virtualenv .venv 2>nul && goto created

:fail
echo Failed to create virtual environment using 'py', 'python -m venv', and 'virtualenv'.
echo Suggestions:
echo  - Ensure Python is installed and 'py' or 'python' is on your PATH.
echo  - If using the Microsoft Store Python, reinstall from python.org and check "Add Python to PATH".
echo  - Temporarily disable antivirus that might block file creation.
echo  - Try running this terminal as Administrator.
echo  - If you see an error about ensurepip, try: python -m ensurepip --default-pip
exit /b 1

:created
echo Virtual environment created in .venv
echo Upgrading pip and installing requirements...
.\.venv\Scripts\python -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
  echo Failed to install some requirements. You can try activating the venv and running pip install -r requirements.txt manually.
)
echo Done.
echo Activate with: .\.venv\Scripts\activate