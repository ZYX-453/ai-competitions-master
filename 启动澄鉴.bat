@echo off
chcp 65001 >nul
rem ===========================================================================
rem  澄鉴 · 一键启动
rem
rem  【为什么不能写死 venv\Scripts\python.exe】
rem  旧版第 5 行是 "%~dp0venv\Scripts\python.exe"。这个 venv 从来没被创建过，
rem  双击的结果是闪一下就退 —— 窗口里只有一行"系统找不到指定的路径"，
rem  用户看到的现象是"点了没反应"，而真正原因藏在被吞掉的错误里。
rem  这里按优先级找一个真能用的解释器，并且把用的是哪一个打印出来，
rem  下次再出问题，看窗口第一行就知道它选了谁。
rem ===========================================================================
setlocal

set "PORT=8848"
set "PY="

rem ① 项目自带的 venv（如果哪天真的建了，优先用它，依赖隔离最干净）
if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"

rem ② 本机 WorkBuddy 的默认环境（当前实际在用的那个，fastapi/pandas/jieba 都齐）
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

rem ③ 系统 Python 3.11（装了 fastapi，但可能缺 jieba）
if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"

rem ④ 最后交给 PATH 里的 python
if not defined PY set "PY=python"

echo.
echo   澄鉴 · 归因诊断 Agent
echo   解释器：%PY%
echo   端口：  http://127.0.0.1:%PORT%
echo.

rem 【先探一下依赖，缺什么当场说清楚】
"%PY%" -c "import fastapi,uvicorn,pandas,numpy,jieba" 2>nul
if errorlevel 1 (
  echo   [!] 这个解释器缺依赖，需要的五个包：
  echo       fastapi uvicorn pandas numpy jieba
  echo.
  echo   装法：
  echo       "%PY%" -m pip install -r "%~dp0requirements.txt"
  echo.
  pause
  exit /b 1
)

cd /d "%~dp0agent\backend"

rem 【端口被占用时不要静默失败】
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul
if not errorlevel 1 (
  echo   [!] 端口 %PORT% 已被占用 —— 可能已经在另一个窗口跑着了。
  echo       直接打开页面看看：
  echo.
  start "" "http://127.0.0.1:%PORT%"
  echo.
  pause
  exit /b 0
)

start "" "http://127.0.0.1:%PORT%"
"%PY%" -W ignore -m uvicorn main:app --host 127.0.0.1 --port %PORT%

rem 走到这里说明 uvicorn 退出了。按任意键关窗口，不然报错一闪而过。
echo.
echo   服务已停止。上面是最后的输出。
pause