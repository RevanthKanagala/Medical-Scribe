@echo off
REM Start the FastAPI server (assumes .venv exists)
if not exist .venv (
  echo venv not found. Run setup_venv.bat first.
  exit /b 1
)
set OPENAI_API_KEY=%OPENAI_API_KEY%
if not exist .venv (
  echo venv not found. Run setup_venv.bat first.
  exit /b 1
)
.\.venv\Scripts\uvicorn app.openai_server:app --reload --host 127.0.0.1 --port 8000