#!/bin/zsh
cd "$(dirname "$0")"
# 先停掉占用 8765 端口的旧服务，避免端口冲突导致启动失败
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null
pkill -f "server.py" 2>/dev/null
sleep 1
# 智能导入助手需要 openpyxl/pdfplumber：优先使用项目内虚拟环境，缺依赖时自动安装
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
open "http://localhost:8765"
.venv/bin/python server.py
