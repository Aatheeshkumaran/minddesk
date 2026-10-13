@echo off
title MindDesk - Local AI
cd /d "%~dp0"
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
echo.
echo Starting MindDesk at http://127.0.0.1:8000
echo Keep Ollama running in the background.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
