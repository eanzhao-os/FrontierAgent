#!/usr/bin/env bash
# ==============================================================================
# FrontierAgent Web UI Boot Script
# Starts the FrontierAgent Web UI directly on port 3030.
# Workflow modes (ReAct / Agent Team) can be switched directly in the Web UI.
# Automatically detects and kills existing processes occupying port 3030.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Handle whether boot.sh is at root or in scripts/
if [ -f "$SCRIPT_DIR/server.py" ]; then
  FRONTIER_DIR="$SCRIPT_DIR"
else
  FRONTIER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

PORT=3030
SELECTED_MODE="react"

# Function to kill process occupying a port
kill_port_if_occupied() {
  local port="$1"
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "⚠️ 检测到端口 :$port 已被占用 (PID: $pids)，正在终止旧进程..."
    kill -9 $pids 2>/dev/null || true
    sleep 0.5
    echo "✅ 端口 :$port 已释放。"
  fi
}

# Parse CLI arguments (optional overrides)
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --mode|-m) SELECTED_MODE="$2"; shift ;;
    --react|-r) SELECTED_MODE="react" ;;
    --team|-t|--agent-team) SELECTED_MODE="agent_team" ;;
    --port|-p) PORT="$2"; shift ;;
    --help|-h)
      echo "Usage: ./boot.sh [options]"
      echo ""
      echo "Options:"
      echo "  --mode, -m <mode>   Initial workflow mode (default: react)"
      echo "  --port, -p <port>   Web UI server port (default: 3030)"
      echo "  --help, -h          Show this help message"
      exit 0
      ;;
    *) echo "Unknown parameter passed: $1"; exit 1 ;;
  esac
  shift
done

# Clean up port conflicts before starting
kill_port_if_occupied "$PORT"

echo "=================================================================="
echo " 🚀 FrontierAgent Web UI 正在启动..."
echo "    - 默认模式: $SELECTED_MODE (可在 Web 界面顶栏随时切换 ReAct / Agent Team)"
echo "    - 访问地址: http://localhost:$PORT"
echo "=================================================================="
echo ""

# Cleanup handler on exit
cleanup() {
  echo ""
  echo "🛑 正在停止 FrontierAgent Web 服务..."
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  echo "👋 服务已全部停止。"
}
trap cleanup EXIT INT TERM

# 1. Start FrontierAgent Python Web Server
cd "$FRONTIER_DIR"
export PYTHONPATH="$FRONTIER_DIR:$PYTHONPATH"
uv run --directory "$FRONTIER_DIR" python "$FRONTIER_DIR/server.py" --mode "$SELECTED_MODE" --port "$PORT" &
SERVER_PID=$!

# Wait for server to become responsive
echo "⏳ 等待服务就绪..."
for i in {1..30}; do
  if curl -s "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
    echo "✅ 服务已就绪！"
    break
  fi
  sleep 0.5
done

UI_URL="http://localhost:$PORT"
echo ""
echo "🎉 FrontierAgent Web UI 启动成功！"
echo "🌐 访问地址: $UI_URL"
echo ""

# Open browser if on macOS or Linux
if command -v open >/dev/null 2>&1; then
  open "$UI_URL" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$UI_URL" || true
fi

echo "💡 提示：在 Web UI 顶栏可随时点击切换 ReAct 模式 与 Agent Team 模式。"
echo "按 Ctrl+C 即可退出服务。"
echo ""

# Keep running
wait
