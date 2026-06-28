#!/usr/bin/env bash
# VPS 一键部署脚本（Ubuntu 22.04 / Debian 12）
# 用法：scp deploy.sh user@vps:~ && ssh user@vps bash deploy.sh

set -euo pipefail

# ── 1. 系统依赖 ──────────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y python3-pip python3-venv git \
  libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
  libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libx11-6 libxext6 \
  libxss1 fonts-liberation wget ca-certificates

# ── 2. 项目目录 ──────────────────────────────────────────────────────────
mkdir -p /opt/scraper_service
cp main.py requirements.txt /opt/scraper_service/
cd /opt/scraper_service

python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/playwright install chromium --with-deps

# ── 3. systemd 服务 ──────────────────────────────────────────────────────
# 先设好 API key（改这里）
API_KEY="${SCRAPER_API_KEY:-changeme_set_your_key}"

cat > /etc/systemd/system/scraper.service <<EOF
[Unit]
Description=TGB Scraper Service
After=network.target

[Service]
User=root
WorkingDirectory=/opt/scraper_service
Environment="SCRAPER_API_KEY=${API_KEY}"
ExecStart=/opt/scraper_service/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8765 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable scraper
systemctl restart scraper

sleep 3
systemctl status scraper --no-pager
echo ""
echo "服务已启动，测试："
echo "  curl http://localhost:8765/health"
