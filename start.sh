#!/usr/bin/env bash
# Khởi động Assets Factory: Flask API (:5000) + Remotion Studio (:3000).
# Dùng:  HOST=127.0.0.1 PORT=5000 bash start.sh
# Dừng:  kill $(cat .app.pid) $(cat .studio.pid)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
export HOST PORT
export PUBLIC_BASE="${PUBLIC_BASE:-http://$HOST:$PORT}"

PY="python3"
[ -x .venv/bin/python ] && PY=".venv/bin/python"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Thiếu lệnh: $1. Chạy 'bash setup_ubuntu.sh' trước."; exit 1; }
}
need ffmpeg
need ffprobe
need node
need npx
"$PY" -c "import flask" 2>/dev/null || { echo "Chưa cài deps Python. Chạy 'bash setup_ubuntu.sh' trước."; exit 1; }

mkdir -p uploads assets/audio assets/video

nohup "$PY" app.py > app.log 2>&1 &
echo $! > .app.pid

cd remotion
nohup npx remotion studio --no-open --port 3000 > ../studio.log 2>&1 &
echo $! > ../.studio.pid
cd ..

sleep 3
echo "UI:      http://$HOST:$PORT"
echo "Studio:  http://$HOST:3000/FactoryCut"
echo "Log:     app.log / studio.log"
echo "Dừng:    kill $(cat .app.pid) $(cat .studio.pid)"