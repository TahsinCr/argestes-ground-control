"""Video kayıt modülü - OpenCV VideoWriter ile timestamp overlay.

Özellikler:
1. İlk lock anından itibaren video kaydı başlat
2. Frame üzerine timestamp (senkronize) ve lock durumu bas
3. Belirli süre (örn. 10sn) veya lock sonuna kadar kaydet
4. Video dosyası adı: lock_baslangic_zamani.mp4

TEKNOFEST 2026:
- 1080p @ 30 FPS H.264 kayıt
- Frame köşesinde UTC zaman damgası
- Lock başlangıcı/bitişi overlay'de gösterilir
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class VideoLogger:
    """Hedef kilitlendiğinde video kaydı yapan modül.

    Kullanım:
        logger = VideoLogger(output_dir="recordings", fps=30.0)
        logger.start_recording(frame, timestamp_ms=1234567890)
        logger.write_frame(frame, timestamp_ms=..., lock_status={...})
        logger.stop_recording()
    """

    def __init__(
        self,
        output_dir: str = "recordings",
        fps: float = 30.0,
        codec: str = "avc1",
        max_duration_sec: float = 60.0,
        draw_overlay: bool = True,
    ):
        """
        Args:
            output_dir: Video kayıtları için dizin
            fps: Kayıt FPS değeri
            codec: FourCC codec (avc1=H.264, mp4v=MPEG-4, XVID=Xvid)
            max_duration_sec: Maksimum kayıt süresi (saniye)
            draw_overlay: Frame üzerine timestamp/lock durumu yazılsın mı?
        """
        self.output_dir = output_dir
        self.fps = fps
        self.codec = codec
        self.max_duration_sec = max_duration_sec
        self.draw_overlay = draw_overlay

        self._writer: Optional[cv2.VideoWriter] = None
        self._recording = False
        self._start_time = 0.0
        self._frame_count = 0
        self._current_file: Optional[str] = None

        # Dizin yoksa oluştur
        os.makedirs(output_dir, exist_ok=True)
        logger.info("VideoLogger: dir=%s fps=%.1f codec=%s max=%.1fs",
                     output_dir, fps, codec, max_duration_sec)

    def _generate_filename(self) -> str:
        """Zaman damgalı video dosyası adı oluştur.

        Returns:
            Tam dosya yolu
        """
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")[:-3]  # ms hassasiyet
        filename = f"lock_{timestamp}.mp4"
        return os.path.join(self.output_dir, filename)

    def _get_fourcc(self) -> int:
        """Codec string'inden FourCC kodu oluştur."""
        codec_map = {
            "avc1": cv2.VideoWriter_fourcc(*"avc1"),
            "mp4v": cv2.VideoWriter_fourcc(*"mp4v"),
            "x264": cv2.VideoWriter_fourcc(*"x264"),
            "XVID": cv2.VideoWriter_fourcc(*"XVID"),
            "MJPG": cv2.VideoWriter_fourcc(*"MJPG"),
            "H264": cv2.VideoWriter_fourcc(*"H264"),
        }
        return codec_map.get(self.codec.lower(), cv2.VideoWriter_fourcc(*"avc1"))

    def start_recording(self, frame: np.ndarray, timestamp_ms: Optional[int] = None) -> bool:
        """Video kaydını başlat.

        Args:
            frame: İlk frame (boyut bilgisi için)
            timestamp_ms: Kayıt başlangıç zamanı (opsiyonel)

        Returns:
            Başlatma başarılı mı?
        """
        if self._recording:
            logger.warning("VideoLogger: zaten kayit yapiliyor")
            return False

        if frame is None or frame.size == 0:
            logger.error("VideoLogger: gecersiz frame")
            return False

        h, w = frame.shape[:2]
        filepath = self._generate_filename()
        fourcc = self._get_fourcc()

        self._writer = cv2.VideoWriter(filepath, fourcc, self.fps, (w, h))
        if not self._writer.isOpened():
            logger.error("VideoLogger: VideoWriter acilamadi: %s", filepath)
            self._writer = None
            return False

        self._recording = True
        self._start_time = time.time()
        self._frame_count = 0
        self._current_file = filepath

        logger.info("VideoLogger: kayit basladi -> %s (%dx%d @ %.1f FPS)",
                     filepath, w, h, self.fps)

        # İlk frame'i yaz
        self.write_frame(frame, timestamp_ms=timestamp_ms)
        return True

    def write_frame(
        self,
        frame: np.ndarray,
        timestamp_ms: Optional[float] = None,
        lock_status: Optional[dict] = None,
    ) -> bool:
        """Frame'i videoya ekle.

        Args:
            frame: BGR formatında frame
            timestamp_ms: Zaman damgası (ms)
            lock_status: Kilit durumu bilgisi (overlay için)

        Returns:
            Yazma başarılı mı?
        """
        if not self._recording or self._writer is None:
            return False

        # Maksimum süre kontrolü
        if time.time() - self._start_time > self.max_duration_sec:
            logger.info("VideoLogger: max sure asildi (%.1fs)", self.max_duration_sec)
            self.stop_recording()
            return False

        # Overlay çiz
        if self.draw_overlay:
            frame = self._draw_overlay(frame, timestamp_ms, lock_status)

        self._writer.write(frame)
        self._frame_count += 1
        return True

    def _draw_overlay(
        self,
        frame: np.ndarray,
        timestamp_ms: Optional[float] = None,
        lock_status: Optional[dict] = None,
    ) -> np.ndarray:
        """Frame üzerine timestamp ve lock durumunu yaz.

        Args:
            frame: BGR frame (modify edilir)
            timestamp_ms: Zaman damgası
            lock_status: Kilit durumu

        Returns:
            Overlay eklenmiş frame
        """
        h, w = frame.shape[:2]

        # Zaman damgası (sol üst)
        if timestamp_ms is not None:
            dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"
        else:
            time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC"

        cv2.putText(
            frame, time_str, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2,
        )

        # Lock durumu (sağ üst)
        if lock_status:
            state = lock_status.get("state", "?")
            lock_active = lock_status.get("lock_active", False)
            confidence = lock_status.get("confidence", 0.0)
            target_id = lock_status.get("target_id", 0)

            color = (0, 255, 0) if lock_active else (0, 165, 255)
            status_text = f"ID:{target_id} {state} conf:{confidence:.2f}"

            # Boyut hesapla
            (tw, th), _ = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.putText(
                frame, status_text, (w - tw - 10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2,
            )

        # Kayıt göstergesi (sol alt)
        elapsed = time.time() - self._start_time
        rec_text = f"REC {self._frame_count} frames ({elapsed:.1f}s)"
        cv2.putText(
            frame, rec_text, (10, h - 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2,
        )

        return frame

    def stop_recording(self) -> Optional[str]:
        """Video kaydını durdur ve dosyayı kapat.

        Returns:
            Kaydedilen dosya yolu veya None
        """
        if not self._recording or self._writer is None:
            return None

        self._writer.release()
        self._writer = None
        self._recording = False

        filepath = self._current_file
        duration = time.time() - self._start_time

        logger.info(
            "VideoLogger: kayit durdu -> %s (%d frame, %.1f sn)",
            filepath, self._frame_count, duration,
        )

        self._current_file = None
        return filepath

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def elapsed_sec(self) -> float:
        if not self._recording:
            return 0.0
        return time.time() - self._start_time