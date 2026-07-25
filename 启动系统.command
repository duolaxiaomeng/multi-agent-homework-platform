#!/bin/zsh
cd "$(dirname "$0")"
# 先停掉占用 8765 端口的旧服务，避免端口冲突导致启动失败
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null
pkill -f "server.py" 2>/dev/null
sleep 1
open "http://localhost:8765"
python3 server.py
