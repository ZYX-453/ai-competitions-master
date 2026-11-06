@echo off
rem 澄鉴 · 一键启动
cd /d "%~dp0agent\backend"
start "" "http://127.0.0.1:8848"
"%~dp0venv\Scripts\python.exe" -m uvicorn main:app --port 8848
