#!/bin/bash
# Deploy agentic_os to your existing DigitalOcean droplet as a systemd service.
# Run this from your local machine.
#
# Usage: ./deploy.sh user@your-droplet-ip

set -euo pipefail

REMOTE="${1:?Usage: ./deploy.sh user@your-droplet-ip}"
REMOTE_DIR="/opt/agentic_os"

echo "==> Syncing files to $REMOTE:$REMOTE_DIR"
ssh "$REMOTE" "mkdir -p $REMOTE_DIR"
rsync -avz --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' \
    ./ "$REMOTE:$REMOTE_DIR/"

echo "==> Installing dependencies"
ssh "$REMOTE" "cd $REMOTE_DIR && pip3 install -r requirements.txt"

echo "==> Writing systemd service"
ssh "$REMOTE" "cat > /etc/systemd/system/agentic-os.service" << 'SERVICE'
[Unit]
Description=Taran's Agentic OS
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/agentic_os
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=10
EnvironmentFile=/opt/agentic_os/.env

[Install]
WantedBy=multi-user.target
SERVICE

ssh "$REMOTE" "systemctl daemon-reload && systemctl enable agentic-os && systemctl restart agentic-os"
echo "==> Done. Check status: ssh $REMOTE 'journalctl -u agentic-os -f'"
