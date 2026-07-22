#!/usr/bin/env bash
set -euo pipefail

VIDEO_NUMBER=20
VIDEO_DEVICE="/dev/video${VIDEO_NUMBER}"
MODULES_FILE="/etc/modules-load.d/kirlangic-galaxy-camera.conf"
OPTIONS_FILE="/etc/modprobe.d/kirlangic-galaxy-camera.conf"
UDEV_FILE="/etc/udev/rules.d/99-kirlangic-galaxy-camera.rules"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

if ! modinfo v4l2loopback >/dev/null 2>&1; then
  echo "HATA: v4l2loopback kernel modülü bulunamadı." >&2
  echo "Arch/EndeavourOS: v4l2loopback-dkms ve kullandığınız kernele uygun headers paketini kurun (ornegin linux-lts-headers)." >&2
  exit 1
fi

install -d -m 0755 /etc/modules-load.d /etc/modprobe.d /etc/udev/rules.d
printf '%s\n' 'v4l2loopback' >"${MODULES_FILE}"
printf '%s\n' 'options v4l2loopback video_nr=20 card_label="Galaxy S25 Ultra" exclusive_caps=1' >"${OPTIONS_FILE}"
printf '%s\n' 'SUBSYSTEM=="video4linux", KERNEL=="video20", GROUP="video", MODE="0660", TAG+="uaccess"' >"${UDEV_FILE}"

udevadm control --reload-rules

if [[ -d /sys/module/v4l2loopback && ! -e "${VIDEO_DEVICE}" ]]; then
  echo "HATA: v4l2loopback farklı ayarlarla zaten yüklü." >&2
  echo "Açık kamera uygulamalarını kapatıp sistemi yeniden başlatın; kalıcı ayarlar açılışta uygulanacak." >&2
  exit 2
fi

if [[ ! -d /sys/module/v4l2loopback ]]; then
  modprobe v4l2loopback \
    video_nr="${VIDEO_NUMBER}" \
    card_label="Galaxy S25 Ultra" \
    exclusive_caps=1
fi

udevadm trigger --subsystem-match=video4linux
udevadm settle

if [[ ! -c "${VIDEO_DEVICE}" ]]; then
  echo "HATA: ${VIDEO_DEVICE} karakter aygıtı oluşmadı." >&2
  exit 3
fi

echo "Galaxy S25 Ultra sanal kamerası hazır: ${VIDEO_DEVICE}"
echo "YKİ açıldığında ADB/scrcpy aktarımı otomatik başlatılacak."
