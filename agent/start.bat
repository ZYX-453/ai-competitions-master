@echo off
setlocal

REM ===================================================================
REM  差评炼金炉 · 一键启动
REM
REM  设计取舍：
REM  1) 不用 PowerShell 写启动逻辑 —— 执行策略不一定允许，双击容易报错。
REM     .bat 是最保守的选择，任何 Windows 都能跑。
REM  2) 先构建前端再启后端：后端把 frontend/dist 挂成静态目录，
REM     只启后端就是完整单端口应用，不需要同时开两个服务。
REM  3) 本文件必须以 GBK + CRLF 保存，否则中文会被 cmd 当成命令执行。
REM     修改后用 _to_gbk.py 重新转码。
REM ===================================================================

cd /d "%~dp0"

echo.
echo   ============================================
echo    差评炼金炉 · 归因诊断 Agent
echo   ============================================
echo.

REM ---------- 1. 定位 Python ----------
set "PY="
if exist "C:\Program Files\Python311\python.exe" set "PY=C:\Program Files\Python311\python.exe"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [x] 没找到 Python 3.11+
  echo       请安装后重试，或手动修改本文件中的 PY 变量。
  pause
  exit /b 1
)
echo   [1/3] Python: %PY%

REM ---------- 2. 前端产物 ----------
if not exist "frontend\dist\index.html" (
  echo   [2/3] 未发现前端产物，开始构建...
  if not exist "frontend\node_modules" (
    echo         首次运行需安装依赖，约 1-2 分钟
    pushd frontend
    call npm install --silent
    popd
  )
  pushd frontend
  call npm run build
  popd
  if not exist "frontend\dist\index.html" (
    echo   [x] 前端构建失败
    pause
    exit /b 1
  )
) else (
  echo   [2/3] 前端产物已就绪
)

REM ---------- 3. 启动 ----------
echo   [3/3] 启动服务...
echo.
echo   访问地址:  http://127.0.0.1:8848
echo   停用服务:  关闭本窗口，或按 Ctrl+C
echo.

cd backend
"%PY%" -m uvicorn main:app --host 127.0.0.1 --port 8848

endlocal
pause