"""Lost frame limit aşıldığında hedefi yeniden yakalama modülü.

Strateji:
1. Önceki frame'deki EKF tahmin bölgesinde (genişletilmiş ROI) arama yap
2. ROI içinde en yüksek güvenli detection'ı seç
3. ROI dışında da tam frame taraması yap
4. Hiçbiri yoksa None dön
"""

import logging
from typing import Any, List, Optional, Tuple

import numpy as np

from target_tracker.perception import YoloInfer

logger = logging.getLogger(__name__)


class Redetect:
    """Kayıp hedefi yeniden yakalamak için ROI bazlı arama modülü.

    Kullanım:
        redetector = Redetect(scaling_factor=1.5, min_confidence=0.25)
        result = redetector.attempt(frame, predicted_bbox=(cx, cy, w, h))
    """

    def __init__(
        self,
        scaling_factor: float = 1.5,
        min_confidence: float = 0.25,
    ):
        """
        Args:
            scaling_factor: Tahmin bölgesinin genişletme faktörü
            min_confidence: Yeniden yakalama için minimum güven skoru
        """
        self.scaling_factor = scaling_factor
        self.min_confidence = min_confidence
        logger.info("Redetect: scale=%.1f min_conf=%.2f", scaling_factor, min_confidence)

    def _extract_roi(self, frame: np.ndarray, predicted_bbox: Tuple[float, float, float, float]) -> np.ndarray:
        """Tahmin edilen bbox etrafında genişletilmiş ROI bölgesi kes.

        Args:
            frame: Tam frame (H, W, 3)
            predicted_bbox: (cx, cy, w, h) formatında tahmin

        Returns:
            ROI bölgesi (H_roi, W_roi, 3)
        """
        h_frame, w_frame = frame.shape[:2]
        cx, cy, w_box, h_box = predicted_bbox

        # Genişletilmiş ROI
        roi_w = int(w_box * self.scaling_factor)
        roi_h = int(h_box * self.scaling_factor)
        roi_w = min(roi_w, w_frame)
        roi_h = min(roi_h, h_frame)

        # ROI koordinatlarını frame sınırlarına kırp
        x1 = max(0, int(cx - roi_w // 2))
        y1 = max(0, int(cy - roi_h // 2))
        x2 = min(w_frame, int(cx + roi_w // 2))
        y2 = min(h_frame, int(cy + roi_h // 2))

        if x2 <= x1 or y2 <= y1:
            return frame

        return frame[y1:y2, x1:x2]

    def attempt(
        self,
        frame: np.ndarray,
        detector: Optional[YoloInfer] = None,
        predicted_bbox: Optional[Tuple[float, float, float, float]] = None,
    ) -> Optional[List[Any]]:
        """Hedefi yeniden yakalamayı dene.

        İki aşamalı strateji:
        1. Öncelikle tahmin bölgesinde (ROI) ara
        2. ROI'de bulunamazsa tam frame tara

        Args:
            frame: np.ndarray (H, W, 3) BGR
            detector: YoloInfer instance (None ise ROI stratejisi kullanılmaz)
            predicted_bbox: (cx, cy, w, h) EKF tahmini (None ise tam frame tara)

        Returns:
            Detection [x1, y1, x2, y2, conf, cls] veya None
        """
        if frame is None:
            return None

        if detector is None:
            logger.debug("Redetect: detector yok, skip ediliyor")
            return None

        # Aşama 1: ROI bazlı arama (EKF tahmini varsa)
        if predicted_bbox is not None:
            roi = self._extract_roi(frame, predicted_bbox)
            if roi.size > 0 and roi.shape[0] >= 16 and roi.shape[1] >= 16:
                roi_detections = detector.infer(roi)
                # ROI içindeki detection'ları global koordinatlara çevir
                cx, cy, w_box, h_box = predicted_bbox
                roi_w = int(w_box * self.scaling_factor)
                roi_h = int(h_box * self.scaling_factor)
                h_frame, w_frame = frame.shape[:2]
                roi_w = min(roi_w, w_frame)
                roi_h = min(roi_h, h_frame)
                x_offset = max(0, int(cx - roi_w // 2))
                y_offset = max(0, int(cy - roi_h // 2))

                for det in roi_detections:
                    if len(det) >= 5 and det[4] >= self.min_confidence:
                        # ROI koordinatlarından global koordinatlara
                        det[0] = float(det[0] + x_offset)
                        det[1] = float(det[1] + y_offset)
                        det[2] = float(det[2] + x_offset)
                        det[3] = float(det[3] + y_offset)
                        logger.info("Redetect: ROI'de hedef bulundu, conf=%.3f", det[4])
                        return det

        # Aşama 2: Tam frame taraması
        full_detections = detector.infer(frame)
        best_det = None
        best_conf = self.min_confidence

        for det in full_detections:
            if len(det) >= 5 and det[4] >= best_conf:
                best_conf = det[4]
                best_det = det

        if best_det is not None:
            logger.info("Redetect: tam frame'de hedef bulundu, conf=%.3f", best_conf)
            return best_det

        logger.debug("Redetect: hedef bulunamadi")
        return None
