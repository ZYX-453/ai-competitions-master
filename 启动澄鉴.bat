@echo off
rem ===========================================================================
rem  澄鉴 · 一键启动（Windows 唯一入口）
rem
rem  【为什么只保留这一个 .bat】
rem  历史上并存三个入口：根目录的「启动澄鉴.bat」、agent\start.bat、agent\start.sh。
rem  三份逻辑各不相同，于是同一个问题在三处的表现也不一样：
rem    · agent\start.bat 是 GBK 编码且没有 chcp，在 UTF-8 终端里全是乱码，
rem      而它恰好是 README 让用户双击的那个；
rem    · 根目录这个编码是对的、会查端口、会打印解释器，但不构建前端，
rem      拿到干净仓库双击的结果是"能开但页面白屏"；
rem    · start.sh 只服务 bash 环境。
rem  现在把 .bat 收成一份：以根目录这份为基础（它最完整），补上前端构建与
rem  依赖诊断。agent\start.bat 改为转发到这里，避免两份逻辑再次漂移。
rem
rem  【为什么不能写死 venv\Scripts\python.exe】
rem  旧版第 5 行是 "%~dp0venv\Scripts\python.exe"。这个 venv 从来没被创建过，
rem  双击的结果是闪一下就退 —— 窗口里只有一行"系统找不到指定的路径"，
rem  用户看到的现象是"点了没反应"，而真正原因藏在被吞掉的错误里。
rem  这里按优先级找一个真能用的解释器，并且把用的是哪一个打印出来，
rem  下次再出问题，看窗口第一行就知道它选了谁。
rem
rem  【本文件必须存为 GBK + CRLF，且不要加 chcp 65001】
rem  这一条踩过，结论和直觉相反：
rem  cmd.exe 解析 .bat 时用的是**打开文件那一刻的 OEM 代码页**（中文系统为 936/GBK），
rem  而不是 chcp 之后设的那个。于是 UTF-8 存的中文注释会被按 GBK 逐字节切开，
rem  rem 行意外"闭合"，后面半句变成一条独立命令去执行 ——
rem  报一堆 "'xx' is not recognized as an internal or external command"，
rem  而且脚本**照样把服务起起来**，特别难对上因果。
rem  所以：文件存 GBK，去掉 chcp。窗体里显示中文靠系统默认代码页，本来就是对的。
rem ===========================================================================
setlocal

set "PORT=8848"
rem 【%~dp0 自带尾部反斜杠】所以后面统一写 %ROOT%agent\... ，不能再补一个 \
set "ROOT=%~dp0"
set "PY="

rem ① 项目自带的 venv（如果哪天真的建了，优先用它，依赖隔离最干净）
if exist "%ROOT%venv\Scripts\python.exe" set "PY=%ROOT%venv\Scripts\python.exe"

rem ② 本机 WorkBuddy 的默认环境（当前实际在用的那个，fastapi/pandas/jieba 都齐）
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

rem ③ 系统 Python 3.11
if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"

rem ④ 最后交给 PATH 里的 python
if not defined PY set "PY=python"

echo.
echo   澄鉴 · 归因诊断 Agent
echo   解释器：%PY%
echo   端口：  http://127.0.0.1:%PORT%
echo.

rem ---------- 1. 依赖自检 ----------
rem 【jieba 单独报，不要混在五个包里一起说】
rem 它是唯一一个"缺了不会让服务起不来、只会让评论洞察整页 500"的依赖。
rem 混在通用提示里，用户会以为跟自己无关（毕竟服务确实起来了）。
"%PY%" -c "import fastapi,uvicorn,pandas,numpy" 2>nul
if errorlevel 1 (
  echo   [x] 这个解释器缺核心依赖，需要：fastapi uvicorn pandas numpy
  echo.
  echo   装法：
  echo       "%PY%" -m pip install -r "%ROOT%requirements.txt"
  echo.
  pause
  exit /b 1
)
"%PY%" -c "import jieba" 2>nul
if errorlevel 1 (
  echo   [!] 缺 jieba —— 服务能起来，但「评论洞察」页会整页报错 500。
  echo       分词、词云、用户画像全部依赖它。
  echo.
  echo       装法：
  echo         "%PY%" -m pip install -r "%ROOT%requirements.txt"
  echo       或单独装：
  echo         "%PY%" -m pip install jieba
  echo.
  echo       现在继续启动，只是评论洞察页不可用。
  echo.
)

rem ---------- 2. 前端产物 ----------
if not exist "%ROOT%agent\frontend\dist\index.html" (
  echo   [2/3] 未发现前端产物，开始构建（首次约 1-2 分钟）...
  if not exist "%ROOT%agent\frontend\node_modules" (
    echo         先装前端依赖...
    pushd "%ROOT%agent\frontend"
    call npm install --silent
    popd
  )
  pushd "%ROOT%agent\frontend"
  call npm run build
  popd
  if not exist "%ROOT%agent\frontend\dist\index.html" (
    echo   [x] 前端构建失败。看上面 npm 的输出。
    echo       没有 node/npm？装一个 LTS 版再双击本文件：
    echo         https://nodejs.org/zh-cn
    pause
    exit /b 1
  )
  echo   [2/3] 前端构建完成
) else (
  echo   [2/3] 前端产物已就绪
)

cd /d "%ROOT%agent\backend"

rem ---------- 3. 端口检查 ----------
rem 【端口被占用时不要静默失败】
rem 也顺手把页面打开 —— 多半是上一次的窗口还在跑，用户其实只想看看。
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

echo   [3/3] 启动服务...
echo.
start "" "http://127.0.0.1:%PORT%"
"%PY%" -W ignore -m uvicorn main:app --host 127.0.0.1 --port %PORT%

rem 走到这里说明 uvicorn 退出了。按任意键关窗口，不然报错一闪而过。
echo.
echo   服务已停止。上面是最后的输出。
pause