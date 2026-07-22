"""APN Handoff - Shared memory üzerinden görüntü işleme bilgisini sunucuya aktarma.

Mimari:
1. Görüntü işleme kartı (Jetson) YOLO/ByteTrack çıktılarını shared memory'e yazar
2. Sunucu kartı (APN) shared memory'den okur ve telemetriye ekler
3. Çift yönlü: Sunucu da komut/parametre yazabilir

Shared Memory Layout (4KB):
- offset 0:     magic (4 byte) = 0xDEADBEEF
- offset 4:     frame_seq (4 byte unsigned int)
- offset 8:     target_x (2 byte signed short, px)
- offset 10:    target_y (2 byte signed short, px)
- offset 12:    target_w (2 byte unsigned short, px)
- offset 14:    target_h (2 byte unsigned short, px)
- offset 16:    target_id (4 byte unsigned int)
- offset 20:    confidence (4 byte float)
- offset 24:    lock_active (1 byte bool)
- offset 25:    yaw_rate (4 byte float, normalized -1..1)
- offset 29:    pitch_rate (4 byte float, normalized -1..1)
- offset 33:    throttle (4 byte float, 0..1)
- offset 37:    timestamp_ms (8 byte double)
- offset 45:    flags (4 byte bitfield)
- offset 49-4095: reserved

Platformlar arası uyumluluk:
- shared_memory modülü (Python 3.8+)
- multiprocessing.shared_memory kullanılır
"""

import logging
import struct
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Shared Memory sabitleri
SHM_SIZE = 4096
SHM_NAME = "apn_handoff_shm"

# Offset tanımları
OFF_MAGIC = 0
OFF_FRAME_SEQ = 4
OFF_TARGET_X = 8
OFF_TARGET_Y = 10
OFF_TARGET_W = 12
OFF_TARGET_H = 14
OFF_TARGET_ID = 16
OFF_CONFIDENCE = 20
OFF_LOCK_ACTIVE = 24
OFF_YAW_RATE = 25
OFF_PITCH_RATE = 29
OFF_THROTTLE = 33
OFF_TIMESTAMP_MS = 37
OFF_FLAGS = 45

# Format tanımları (struct)
# I=uint32, h=short, H=ushort, f=float, B=uchar, d=double
FMT_MAGIC = "<I"
FMT_FRAME_SEQ = "<I"
FMT_TARGET_XY = "<hh"
FMT_TARGET_WH = "<HH"
FMT_TARGET_ID = "<I"
FMT_CONFIDENCE = "<f"
FMT_LOCK_ACTIVE = "<B"
FMT_YAW_RATE = "<f"
FMT_PITCH_RATE = "<f"
FMT_THROTTLE = "<f"
FMT_TIMESTAMP_MS = "<d"
FMT_FLAGS = "<I"

MAGIC = 0xDEADBEEF


class APNHandoff:
    """Shared memory üzerinden Jetson <-> APN iletişimi.

    Kullanım:
        writer = APNHandoff()
        writer.write_target(cx=320, cy=240, w=80, h=60)
        writer.write_control(yaw_rate=0.3, pitch_rate=-0.1, throttle=0.6)

        reader = APNHandoff()
        data = reader.read()
    """

    def __init__(self, name: str = SHM_NAME, create: bool = True):
        """
        Args:
            name: Shared memory segment adı
            create: True ise yeni segment oluştur, False ise mevcut olana bağlan
        """
        self.name = name
        self._shm = None
        self._buf = None

        try:
            from multiprocessing import shared_memory
            if create:
                self._shm = shared_memory.SharedMemory(name=name, create=True, size=SHM_SIZE)
                logger.info("APNHandoff: yeni shared memory olusturuldu: %s", name)
            else:
                self._shm = shared_memory.SharedMemory(name=name)
                logger.info("APNHandoff: mevcut shared memory'e baglanildi: %s", name)

            self._buf = self._shm.buf
            # İlk yazmada magic ve sıfırlama
            if create:
                self._write_magic()

        except Exception as e:
            logger.warning("APNHandoff: shared memory olusturulamadi: %s", e)
            logger.warning("APNHandoff: fallback mode (bellek yok)")
            self._buf = None

    def _write_magic(self):
        """Magic number ve segment başlangıç sıfırlama."""
        if self._buf is None:
            return
        try:
            struct.pack_into(FMT_MAGIC, self._buf, OFF_MAGIC, MAGIC)
            struct.pack_into(FMT_FRAME_SEQ, self._buf, OFF_FRAME_SEQ, 0)
            # Tüm buffer'ı sıfırla (magic hariç)
            for i in range(OFF_MAGIC + 4, SHM_SIZE):
                self._buf[i] = 0
        except Exception as e:
            logger.error("APNHandoff: magic yazma hatasi: %s", e)

    def __del__(self):
        """Temizlik: shared memory segmentini kapat."""
        self.close()

    def close(self):
        """Shared memory bağlantısını kapat."""
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm = None
                self._buf = None
                logger.debug("APNHandoff: shared memory kapandi")
            except Exception as e:
                logger.warning("APNHandoff: kapatma hatasi: %s", e)

    def write_target(
        self,
        cx: float = 0.0,
        cy: float = 0.0,
        w: float = 0.0,
        h: float = 0.0,
        target_id: int = 0,
        confidence: float = 0.0,
    ):
        """Hedef bilgisini shared memory'e yaz.

        Args:
            cx: Hedef merkez x (piksel)
            cy: Hedef merkez y (piksel)
            w: Hedef genişlik (piksel)
            h: Hedef yükseklik (piksel)
            target_id: Takip ID
            confidence: Detection güven skoru
        """
        if self._buf is None:
            return

        try:
            frame_seq = struct.unpack_from(FMT_FRAME_SEQ, self._buf, OFF_FRAME_SEQ)[0] + 1

            struct.pack_into(FMT_FRAME_SEQ, self._buf, OFF_FRAME_SEQ, frame_seq)
            struct.pack_into(FMT_TARGET_XY, self._buf, OFF_TARGET_X, int(cx), int(cy))
            struct.pack_into(FMT_TARGET_WH, self._buf, OFF_TARGET_W, max(0, int(w)), max(0, int(h)))
            struct.pack_into(FMT_TARGET_ID, self._buf, OFF_TARGET_ID, target_id)
            struct.pack_into(FMT_CONFIDENCE, self._buf, OFF_CONFIDENCE, confidence)
            struct.pack_into(FMT_TIMESTAMP_MS, self._buf, OFF_TIMESTAMP_MS, time.time() * 1000.0)

        except Exception as e:
            logger.error("APNHandoff: target yazma hatasi: %s", e)

    def write_control(
        self,
        yaw_rate: float = 0.0,
        pitch_rate: float = 0.0,
        throttle: float = 0.5,
        lock_active: bool = False,
    ):
        """Kontrol komutlarını shared memory'e yaz.

        Args:
            yaw_rate: Normalize yaw hızı (-1..1)
            pitch_rate: Normalize pitch hızı (-1..1)
            throttle: Gaz (0..1)
            lock_active: Kilit aktif mi?
        """
        if self._buf is None:
            return

        try:
            struct.pack_into(FMT_YAW_RATE, self._buf, OFF_YAW_RATE, yaw_rate)
            struct.pack_into(FMT_PITCH_RATE, self._buf, OFF_PITCH_RATE, pitch_rate)
            struct.pack_into(FMT_THROTTLE, self._buf, OFF_THROTTLE, throttle)
            struct.pack_into(FMT_LOCK_ACTIVE, self._buf, OFF_LOCK_ACTIVE, 1 if lock_active else 0)

        except Exception as e:
            logger.error("APNHandoff: control yazma hatasi: %s", e)

    def publish(self, data: dict):
        """Test uyumluluğu için publish metodu - sözlük verisini shared memory'e yazar.

        Args:
            data: Sözlük formatında veri (state, locked, target_visible, vb.)
        """
        self.write_control(
            yaw_rate=data.get("roll_rate", 0.0),
            pitch_rate=data.get("pitch_rate", 0.0),
            throttle=data.get("throttle", 0.5),
            lock_active=data.get("locked", False),
        )

    def read(self) -> Optional[Dict]:
        """Shared memory'den tüm veriyi oku.

        Returns:
            Sözlük formatında veri veya None
        """
        if self._buf is None:
            return None

        try:
            magic = struct.unpack_from(FMT_MAGIC, self._buf, OFF_MAGIC)[0]
            if magic != MAGIC:
                logger.warning("APNHandoff: magic uyumsuz: 0x%08X", magic)
                return None

            frame_seq = struct.unpack_from(FMT_FRAME_SEQ, self._buf, OFF_FRAME_SEQ)[0]
            cx, cy = struct.unpack_from(FMT_TARGET_XY, self._buf, OFF_TARGET_X)
            w, h = struct.unpack_from(FMT_TARGET_WH, self._buf, OFF_TARGET_W)
            target_id = struct.unpack_from(FMT_TARGET_ID, self._buf, OFF_TARGET_ID)[0]
            confidence = struct.unpack_from(FMT_CONFIDENCE, self._buf, OFF_CONFIDENCE)[0]
            lock_active = bool(struct.unpack_from(FMT_LOCK_ACTIVE, self._buf, OFF_LOCK_ACTIVE)[0])
            yaw_rate = struct.unpack_from(FMT_YAW_RATE, self._buf, OFF_YAW_RATE)[0]
            pitch_rate = struct.unpack_from(FMT_PITCH_RATE, self._buf, OFF_PITCH_RATE)[0]
            throttle = struct.unpack_from(FMT_THROTTLE, self._buf, OFF_THROTTLE)[0]
            timestamp_ms = struct.unpack_from(FMT_TIMESTAMP_MS, self._buf, OFF_TIMESTAMP_MS)[0]

            return {
                "frame_seq": frame_seq,
                "target_x": cx,
                "target_y": cy,
                "target_w": w,
                "target_h": h,
                "target_id": target_id,
                "confidence": confidence,
                "lock_active": lock_active,
                "yaw_rate": yaw_rate,
                "pitch_rate": pitch_rate,
                "throttle": throttle,
                "timestamp_ms": timestamp_ms,
            }

        except Exception as e:
            logger.error("APNHandoff: okuma hatasi: %s", e)
            return None

    def unlink(self):
        """Shared memory segmentini tamamen sil (sadece creator yapmalı)."""
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()
                self._shm = None
                self._buf = None
                logger.info("APNHandoff: shared memory silindi")
            except Exception as e:
                logger.warning("APNHandoff: unlink hatasi: %s", e)