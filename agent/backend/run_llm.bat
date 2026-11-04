@echo off
chcp 936 >nul
REM 本机调试启动脚本（不含 Key，Key 从环境变量读）
REM 用法：先 set CANYU_LLM_KEY=sk-xxx，再双击本文件
set PY=C:////Program Files\Python311\python.exe
if "%CANYU_LLM_KEY%"=="" (
  echo [ERROR] CANYU_LLM_KEY 未设置。请先 set CANYU_LLM_KEY=你的key
  pause
  exit /b 1
)
set CANYU_LLM_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
set CANYU_LLM_MODEL=qwen-plus
cd /d %~dp0
"%PY%" -m uvicorn main:app --host 127.0.0.1 --port 8848
pause
