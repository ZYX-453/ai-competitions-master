#!/usr/bin/env bash
# 差评溯源 · 启动脚本（bash / Git Bash / macOS / Linux）
# Windows 用户双击 start.bat 即可，不必用这个。

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# ---------- 定位 Python ----------
# 顺序按"最可能是对的"排：
#   1) 项目根 .venv        （按 requirements.txt 建的那个，首选）
#   2) 工作区根 .venv      （venv 建在上一层的情况）
#   3) Python311 标准路径  （作者本机）
#   4) PATH 里的 python3 / python
# 之前只写了 3、4，换台机器就 fallback 到系统 Python，然后报 No module named uvicorn。
PY=""
for c in \
  "$HERE/../.venv/Scripts/python.exe" \
  "$HERE/../.venv/bin/python" \
  "$HERE/../../.venv/Scripts/python.exe" \
  "$HERE/../../.venv/bin/python" \
  "C:/Program Files/Python311/python.exe" \
  python3 python
do
  if [ -x "$c" ]; then PY="$c"; break; fi
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[x] 没找到 Python 3.10+"
  echo "    先建环境：python -m venv .venv && .venv/Scripts/pip install -r requirements.txt"
  exit 1
fi
echo "[1/3] Python: $PY"

# 依赖自检：宁可现在报清楚，也不要等 uvicorn 起不来才看到一句 ModuleNotFoundError
if ! "$PY" -c "import fastapi, uvicorn, pandas, numpy, jieba" 2>/dev/null; then
  echo "[x] 这个 Python 缺依赖（fastapi / uvicorn / pandas / numpy / jieba 至少缺一个）"
  echo "    装一下：\"$PY\" -m pip install -r requirements.txt"
  exit 1
fi

# ---------- 前端产物 ----------
if [ ! -f frontend/dist/index.html ]; then
  echo "[2/3] 未发现前端产物，开始构建..."
  # 依赖装过一次就不用再装，node_modules 存在即跳过
  if [ ! -d frontend/node_modules ]; then
    ( cd frontend && npm install )
  fi
  ( cd frontend && npm run build )
else
  echo "[2/3] 前端产物已就绪"
fi

# ---------- 启动 ----------
# 后端同时托管 API 与静态前端，单端口即可访问完整应用。
PORT="${PORT:-8848}"
if command -v netstat >/dev/null 2>&1 && netstat -ano 2>/dev/null | grep -q ":$PORT .*LISTENING"; then
  echo "[x] 端口 $PORT 已被占用。"
  echo "    要么关掉占用进程，要么换个端口：PORT=8849 bash start.sh"
  exit 1
fi
echo "[3/3] 启动服务 → http://127.0.0.1:$PORT"
cd backend
exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"