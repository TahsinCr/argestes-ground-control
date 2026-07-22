#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

rm -f \
  /etc/modules-load.d/kirlangic-galaxy-camera.conf \
  /etc/modprobe.d/kirlangic-galaxy-camera.conf \
  /etc/udev/rules.d/99-kirlangic-galaxy-camera.rules
udevadm control --reload-rules

echo "Galaxy USB kamera kalıcı ayarları kaldırıldı."
echo "Modül kullanımda olabileceği için zorla kaldırılmadı; yeniden başlatma sonrası /dev/video20 oluşmayacak."
