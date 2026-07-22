"""ByteTrack benzeri ID koruma tracker'ı (SORT tabanlı).

Her detection'a bir ID atar ve frame'ler arası IOU + Kalman Filter
ile aynı objeyi takip eder. Düşük güven skorlu tespitleri de
(ikinci aşama) değerlendirerek ID kaybını minimize eder.

Referans: https://arxiv.org/abs/2110.06864 (ByteTrack)

YKİ entegrasyonunda modelden bağımsız, iki aşamalı güven eşiği ve
IoU eşlemesiyle kararlı hedef kimliği üretmek için kullanılır.
"""

import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class KalmanBoxTracker:
    """Tek bir kutu için Kalman Filter tracker'ı.

    State: [cx, cy, s, r, cx_dot, cy_dot, s_dot]
    - cx, cy: merkez koordinatları
    - s: alan (w * h)
    - r: aspect ratio (w / h)
    - dot'lar: hız bileşenleri
    """

    count = 0

    def __init__(self, bbox: Tuple[float, float, float, float]):
        """Yeni bir tracker başlat.

        Args:
            bbox: (x1, y1, x2, y2) formatında bounding box
        """
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        cx, cy = x1 + w / 2.0, y1 + h / 2.0
        s, r = w * h, w / (h + 1e-6)

        self.x = np.array([cx, cy, s, r, 0, 0, 0], dtype=np.float64)
        self.P = np.eye(7, dtype=np.float64) * 10.0

        # Ölçüm matrisi (sadece cx, cy, s, r ölçülür)
        self.H = np.zeros((4, 7), dtype=np.float64)
        self.H[:4, :4] = np.eye(4, dtype=np.float64)

        # İşlem gürültüsü (velocity_noise: 5.0 - Pareto analizi ideal bölge)
        self.Q = np.eye(7, dtype=np.float64) * 0.01
        self.Q[4:, 4:] *= 5.0  # hız bileşenleri, düşük belirsizlik

        # Ölçüm gürültüsü
        self.R = np.eye(4, dtype=np.float64) * 1.0

        # Durum geçiş matrisi (dt=1)
        self.F = np.eye(7, dtype=np.float64)
        self.F[0, 4] = 1  # cx += cx_dot
        self.F[1, 5] = 1  # cy += cy_dot
        self.F[2, 6] = 1  # s += s_dot

        self.id = KalmanBoxTracker.count
        KalmanBoxTracker.count += 1

        self.hit_streak = 1
        self.confirmed = False
        self.age = 0
        self.time_since_update = 0
        self.last_confidence = 1.0

    def predict(self) -> np.ndarray:
        """Bir adım ileri tahmin yap.

        Returns:
            Tahmin edilen state vektörü.
        """
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        self.age += 1
        self.time_since_update += 1
        return self.x

    def update(self, bbox: Tuple[float, float, float, float], confidence: float = 1.0):
        """Ölçüm ile Kalman Filter'ı güncelle.

        Args:
            bbox: (x1, y1, x2, y2)
            confidence: Detection güven skoru
        """
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        cx, cy = x1 + w / 2.0, y1 + h / 2.0
        s, r = w * h, w / (h + 1e-6)

        z = np.array([cx, cy, s, r], dtype=np.float64)

        # Kalman kazancı
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)

        # State güncelleme
        y = z - self.H @ self.x
        self.x = self.x + K @ y
        self.P = (np.eye(7) - K @ self.H) @ self.P

        self.hit_streak += 1
        self.time_since_update = 0
        self.last_confidence = confidence

    def get_state(self) -> Tuple[float, float, float, float]:
        """Mevcut tahmini bbox olarak döndür.

        Returns:
            (x1, y1, x2, y2) formatında bbox
        """
        cx, cy, s, r = self.x[:4]
        # s ve r'nin geçerli olduğundan emin ol
        s = max(s, 1.0)          # en az 1 piksel alan
        r = max(r, 0.1)          # aspect ratio en az 0.1
        w = np.sqrt(s * r)
        h = s / (w + 1e-6)
        x1 = max(0, cx - w / 2.0)
        y1 = max(0, cy - h / 2.0)
        x2 = cx + w / 2.0
        y2 = cy + h / 2.0
        # NaN kontrolü
        if np.isnan(x1) or np.isnan(y1) or np.isnan(x2) or np.isnan(y2):
            return (0.0, 0.0, 0.0, 0.0)
        return (float(x1), float(y1), float(x2), float(y2))


def iou(bbox_a: Tuple[float, float, float, float],
        bbox_b: Tuple[float, float, float, float]) -> float:
    """İki bbox arası IoU hesapla.

    Args:
        bbox_a, bbox_b: (x1, y1, x2, y2)

    Returns:
        0.0 - 1.0 arası IoU
    """
    x1 = max(bbox_a[0], bbox_b[0])
    y1 = max(bbox_a[1], bbox_b[1])
    x2 = min(bbox_a[2], bbox_b[2])
    y2 = min(bbox_a[3], bbox_b[3])

    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter <= 0:
        return 0.0

    area_a = (bbox_a[2] - bbox_a[0]) * (bbox_a[3] - bbox_a[1])
    area_b = (bbox_b[2] - bbox_b[0]) * (bbox_b[3] - bbox_b[1])
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0.0


class ByteTrackWrapper:
    """ByteTrack tarzı iki aşamalı ID atama tracker'ı.

    Birinci aşama: yüksek güvenli (>high_thresh) detection'lar
    İkinci aşama: düşük güvenli (high_thresh > conf > low_thresh) detection'lar
    """

    def __init__(
        self,
        track_high_thresh: float = 0.35,    # yüksek güven eşiği (sahte kilitlenmeyi önle)
        track_low_thresh: float = 0.10,     # düşük güven eşiği (kısa flulaşmalar)
        match_thresh: float = 0.7,          # IOU eşleme eşiği
        new_track_thresh: float = 0.30,     # yeni track açma güven eşiği
        max_lost_frames: int = 30,          # maksimum kayıp frame
        min_hit_streak: int = 4,            # minimum eşleşme sayısı (onaylı track için)
    ):
        """
        Args:
            track_high_thresh: Birinci aşama eşiği (yüksek güven)
            track_low_thresh: İkinci aşama eşiği (düşük güven)
            match_thresh: IOU eşleme threshold'u
            new_track_thresh: Yeni track açma güven eşiği
            max_lost_frames: Bu kadar frame görülmezse track silinir
            min_hit_streak: Track'in onaylanması için gereken minimum eşleşme
        """
        self.track_high_thresh = track_high_thresh
        self.track_low_thresh = track_low_thresh
        self.match_thresh = match_thresh
        self.new_track_thresh = new_track_thresh
        self.max_lost_frames = max_lost_frames
        self.min_hit_streak = min_hit_streak

        # Aktif track'ler: {id: KalmanBoxTracker}
        self.active_tracks: Dict[int, KalmanBoxTracker] = OrderedDict()
        self.frame_id = 0

        logger.info(
            "ByteTrackWrapper: high=%.2f low=%.2f match=%.2f new=%.2f lost=%d min_hit=%d",
            track_high_thresh, track_low_thresh, match_thresh,
            new_track_thresh, max_lost_frames, min_hit_streak,
        )

    def reset(self):
        """Tüm track'leri sıfırla (yeni video için)."""
        self.active_tracks.clear()
        KalmanBoxTracker.count = 0
        self.frame_id = 0
        logger.info("ByteTrackWrapper sifirlandi.")

    def update(self, detections: List[Any]) -> List[Any]:
        """Yeni frame'deki detection'ları işle ve ID ata.

        Args:
            detections: Her biri [x1, y1, x2, y2, confidence, class_id]
                        veya [x1, y1, x2, y2, confidence, class_id, ...] formatında.

        Returns:
            [x1, y1, x2, y2, confidence, class_id, track_id, hit_streak]
            formatında güncellenmiş detection listesi.
        """
        self.frame_id += 1

        # --- 1. Prediction: tüm aktif track'leri bir adım ilerlet ---
        for track in self.active_tracks.values():
            track.predict()

        # --- 2. Detection'ları güven skoruna göre ayır ---
        high_dets = []
        low_dets = []
        for det in detections:
            if len(det) < 5:
                continue
            conf = det[4]
            if conf >= self.track_high_thresh:
                high_dets.append(det)
            elif conf >= self.track_low_thresh:
                low_dets.append(det)

        # --- 3. Track ID'lerini al (aktif track'ler) ---
        track_ids = list(self.active_tracks.keys())

        # --- 4. Birinci aşama: yüksek güvenli detection'ları eşle ---
        matched_high, unmatched_det_idxs_high = self._match_tracks(
            track_ids, high_dets, self.match_thresh
        )

        # --- 5. İkinci aşama: yüksek güvenlide eşleşemeyen track'lere düşük güvenli dene ---
        unmatched_tracks_high = [
            tid for tid in track_ids if tid not in matched_high
        ]
        matched_low, unmatched_det_idxs_low = self._match_tracks(
            unmatched_tracks_high, low_dets, self.match_thresh
        )

        # --- 6. Track'leri güncelle (matched) ---
        for tid, det_idx in matched_high.items():
            det = high_dets[det_idx]
            x1, y1, x2, y2 = det[:4]
            self.active_tracks[tid].update((x1, y1, x2, y2), confidence=det[4])

        for tid, det_idx in matched_low.items():
            det = low_dets[det_idx]
            x1, y1, x2, y2 = det[:4]
            self.active_tracks[tid].update((x1, y1, x2, y2), confidence=det[4])

        # --- 7. Hiç eşleşemeyen yüksek güvenli detection'lardan yeni track aç ---
        newly_created_ids = []
        for det_idx in unmatched_det_idxs_high:
            det = high_dets[det_idx]
            conf = det[4]
            if conf >= self.new_track_thresh:
                x1, y1, x2, y2 = det[:4]
                new_tracker = KalmanBoxTracker((x1, y1, x2, y2))
                self.active_tracks[new_tracker.id] = new_tracker
                newly_created_ids.append(new_tracker.id)

        # --- 8. Çok yaşlı/kayıp track'leri temizle ---
        to_remove = []
        for tid, track in self.active_tracks.items():
            # Eğer bu frame'de eşleşmediyse (yaşlandıysa)
            is_matched = (tid in matched_high) or (tid in matched_low) or (tid in newly_created_ids)
            if not is_matched:
                track.hit_streak = 0

            # Track çok yaşlıysa (belli bir süredir güncelleme almadıysa) sil
            if track.time_since_update > self.max_lost_frames:
                to_remove.append(tid)
            # Eğer yeni açılmış bir track ise ve hemen ilk karelerde kaybolduysa hızlı sil
            elif track.age < self.min_hit_streak and track.time_since_update > 1:
                to_remove.append(tid)

        for tid in to_remove:
            del self.active_tracks[tid]

        # --- 9. Çıktıyı oluştur (sadece aktif ve onaylanmış track'ler) ---
        output = []
        for tid, track in self.active_tracks.items():
            if track.hit_streak >= self.min_hit_streak:
                track.confirmed = True
            # Bir kez doğrulanan hedef, tek bir kaçırılan kareden sonra
            # yeniden min_hit_streak kadar ısınmak zorunda kalmaz. Yine de
            # hayalet kutu çizmemek için yalnızca bu karede ölçüm alan
            # track'i döndürüyoruz.
            if track.time_since_update == 0 and track.confirmed:
                x1, y1, x2, y2 = track.get_state()
                conf = track.last_confidence
                cls_id = 0
                output.append([x1, y1, x2, y2, conf, float(cls_id), int(tid), float(track.hit_streak)])

        return output

    def _match_tracks(
        self,
        track_ids: List[int],
        detections: List[Any],
        threshold: float,
    ) -> Tuple[Dict[int, int], List[int]]:
        """Hungarian benzeri greedy IOU eşleme.

        Her track ile en yüksek IOU'lu detection'ı eşle.
        Bir track birden fazla detection ile eşleşemez.
        Bir detection birden fazla track ile eşleşemez.

        Args:
            track_ids: Eşlenecek track ID'leri
            detections: Detection listesi
            threshold: Minimum IOU eşiği

        Returns:
            (matched: {track_id: detection_idx}, unmatched_det_indices)
        """
        if not track_ids or not detections:
            return {}, list(range(len(detections)))

        # IOU matrisi oluştur
        matched: Dict[int, int] = {}
        used_detections = set()

        # Track'leri en yaşlıdan en gence sırala (öncelik yaşlı track'lerde)
        sorted_tracks = sorted(
            track_ids,
            key=lambda tid: self.active_tracks[tid].hit_streak,
            reverse=True,
        )

        for tid in sorted_tracks:
            track = self.active_tracks[tid]
            track_bbox = track.get_state()
            best_iou = threshold
            best_det_idx = -1

            for i, det in enumerate(detections):
                if i in used_detections:
                    continue
                det_bbox = tuple(det[:4])
                iou_val = iou(track_bbox, det_bbox)
                if iou_val > best_iou:
                    best_iou = iou_val
                    best_det_idx = i

            if best_det_idx >= 0:
                matched[tid] = best_det_idx
                used_detections.add(best_det_idx)

        unmatched_indices = [i for i in range(len(detections)) if i not in used_detections]
        return matched, unmatched_indices
