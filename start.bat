@echo off
cd /d %~dp0
start "" http://127.0.0.1:8001
.venv\Scripts\python -m uvicorn app.main:app --port 8001
