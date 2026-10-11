#!/usr/bin/env bash
# Cài đặt mọi phụ thuộc cho Assets Factory trên Ubuntu 22.04 / 24.04.
# Chạy 1 lần:  bash setup_ubuntu.sh
set -euo pipefail

sudo apt update
sudo apt install -y python3 python3-pip python3-venv ffmpeg curl git

# Node.js 22 (Remotion cần Node 18+, nên dùng bản 22 LTS)
NEED_NODE=0
if ! command -v node >/dev/null 2>&1; then
  NEED_NODE=1
else
  MAJOR="$(node -v | cut -d. -f1 | tr -dc 0-9)"
  [ "${MAJOR:-0}" -lt 18 ] && NEED_NODE=1
fi
if [ "$NEED_NODE" = "1" ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt install -y nodejs
fi

# Thư viện hệ thống cho Chrome Headless Shell (Remotion render).
# Nguồn: https://www.remotion.dev/docs/miscellaneous/linux-dependencies
sudo apt install -y \
  libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
  libxrandr2 libxkbcommon-dev libxfixes3 libxcomposite1 libxdamage1 \
  libgbm-dev libcups2 libcairo2 libpango-1.0-0
( sudo apt install -y libasound2t64 || sudo apt install -y libasound2 )

# Môi trường Python riêng của dự án
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Deps Node + trình duyệt cho Remotion
cd remotion
npm i
npx remotion browser ensure
cd ..

echo "Xong. Chạy:  bash start.sh"