#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/home/kirlangic/kirlangic-orin-link"
SERVICE_NAME="kirlangic-orin-link.service"
UNIT_SOURCE="$INSTALL_DIR/$SERVICE_NAME"
UNIT_TARGET="/etc/systemd/system/$SERVICE_NAME"

if [[ "$(id -un)" != "kirlangic" ]]; then
  echo "Bu script kirlangic kullanıcısı olarak çalıştırılmalı." >&2
  exit 1
fi

for required in orin_mavlink_agent.py config.json "$SERVICE_NAME"; do
  if [[ ! -f "$INSTALL_DIR/$required" ]]; then
    echo "Eksik kurulum dosyası: $INSTALL_DIR/$required" >&2
    exit 1
  fi
done

/usr/bin/python3 -c 'import pymavlink'
/usr/bin/python3 "$INSTALL_DIR/orin_mavlink_agent.py" \
  --config "$INSTALL_DIR/config.json" \
  --check-config

sudo install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true

echo "Otomatik başlangıç etkin: $SERVICE_NAME"
