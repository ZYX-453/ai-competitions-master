#!/usr/bin/env bash
# 差评炼金炉 · 启动脚本（bash / Git Bash / macOS / Linux）
# Windows 用户双击 start.bat 即可，不必用这个。

set -euo pipefail
cd "$(dirname "$0")"

# ---------- 定位 Python ----------
PY=""
for c in "C:/Program Files/Python311/python.exe" python3 python; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[x] 没找到 Python 3.11+"; exit 1
fi
echo "[1/3] Python: $PY"

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
echo "[3/3] 启动服务 → http://127.0.0.1:8848"
cd backend
exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port 8848