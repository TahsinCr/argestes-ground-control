#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="kirlangic-orin-link.service"
UNIT_TARGET="/etc/systemd/system/$SERVICE_NAME"

sudo systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
sudo rm -f "$UNIT_TARGET"
sudo systemctl daemon-reload
sudo systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

echo "Otomatik başlangıç kaldırıldı. /home/kirlangic/kirlangic-orin-link içindeki dosyalar korundu."
