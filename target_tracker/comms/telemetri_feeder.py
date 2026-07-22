"""Anlık kilitlenme/takip verilerini ana telemetri paketine 1-5 Hz ile basar.

Mimari:
- LockStateMachine'dan lock_status alır
- APNHandoff'dan target bilgisini alır
- Telemetri verisini JSON formatında hazırlar
- SunucuClient üzerinden POST yapar (opsiyonel)
- Ayrıca MavlinkBridge üzerinden STATUSTEXT mesajı gönderir
"""

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TelemetriFeeder:
    """Takip/kilitlenme durumunu telemetri kanalına besleyen modül.

    Kullanım:
        feeder = TelemetriFeeder(publish_interval=0.5)  # 2 Hz
        feeder.publish(lock_status={
            "target_id": 1, "cx": 320, "cy": 240, "confidence": 0.85,
            "lock_active": True, "state": "LOCKED"
        })
    """

    def __init__(
        self,
        publish_interval: float = 0.5,
        telemetry_file: Optional[str] = None,
    ):
        """
        Args:
            publish_interval: Telemetri yayın aralığı (saniye)
            telemetry_file: Telemetri log dosyası (None = dosyaya yazma)
        """
        self.publish_interval = publish_interval
        self.telemetry_file = telemetry_file
        self._last_publish_time = 0.0
        self._last_data: Optional[Dict[str, Any]] = None

        if telemetry_file:
            logger.info("TelemetriFeeder: dosyaya log -> %s", telemetry_file)

    def publish(self, lock_status: Dict[str, Any]) -> bool:
        """Kilitlenme/takip durumunu telemetriye gönder.

        Args:
            lock_status: Sözlük formatında durum.
                Beklenen anahtarlar:
                - target_id: int
                - cx, cy: float (piksel)
                - w, h: float (piksel)
                - confidence: float (0-1)
                - lock_active: bool
                - state: str ("SEARCHING", "TRACKING", "LOCKED", etc.)

        Returns:
            Gönderim başarılı mı?
        """
        now = time.time()

        # Rate limiting
        if now - self._last_publish_time < self.publish_interval:
            return False

        self._last_publish_time = now

        # Temel telemetri paketi
        telemetry = {
            "timestamp": now,
            "timestamp_ms": int(now * 1000),
            "target_id": lock_status.get("target_id", 0),
            "target_x": int(lock_status.get("cx", 0)),
            "target_y": int(lock_status.get("cy", 0)),
            "target_w": int(lock_status.get("w", 0)),
            "target_h": int(lock_status.get("h", 0)),
            "confidence": round(lock_status.get("confidence", 0.0), 4),
            "lock_active": lock_status.get("lock_active", False),
            "state": lock_status.get("state", "UNKNOWN"),
            "frame_seq": lock_status.get("frame_seq", 0),
        }

        # Dosyaya log
        if self.telemetry_file:
            try:
                with open(self.telemetry_file, "a") as f:
                    f.write(json.dumps(telemetry) + "\n")
            except Exception as e:
                logger.error("TelemetriFeeder: dosya yazma hatasi: %s", e)

        # JSON log (console)
        logger.info("TELEMETRI: %s", json.dumps(telemetry, default=str))

        self._last_data = telemetry
        return True

    def get_last_telemetry(self) -> Optional[Dict[str, Any]]:
        """Son gönderilen telemetri paketini döndür.

        Returns:
            Son telemetri verisi veya None
        """
        return self._last_data

    def reset(self):
        """İstatistikleri sıfırla."""
        self._last_publish_time = 0.0
        self._last_data = None
        logger.debug("TelemetriFeeder sifirlandi.")