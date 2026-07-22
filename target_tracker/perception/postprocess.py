"""NMS ve bbox verilerinden merkez koordinat çıkarımı.

IoU hesaplama ve Non-Maximum Suppression (NMS) implementasyonu.
"""

from typing import List, Tuple


def iou(box_a: List[float], box_b: List[float]) -> float:
    """İki bbox arasındaki Intersection over Union (IoU) oranı.

    Args:
        box_a: [x1, y1, x2, y2]
        box_b: [x1, y1, x2, y2]

    Returns:
        0.0 - 1.0 arası IoU değeri.
    """
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter <= 0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0.0


def non_max_suppression(detections: List, iou_threshold: float = 0.45) -> List:
    """Non-Maximum Suppression: çakışan kutulardan en yüksek güvenli olanı tutar.

    Args:
        detections: Her biri [x1, y1, x2, y2, confidence, class_id] formatında liste.
        iou_threshold: IoU eşiği (üzeri çakışma kabul edilir).

    Returns:
        Filtrelenmiş detection listesi.
    """
    if not detections:
        return []

    # Güvene göre azalan sırala
    sorted_dets = sorted(detections, key=lambda d: d[4], reverse=True)
    kept = []

    while sorted_dets:
        best = sorted_dets.pop(0)
        kept.append(best)

        # Kalanları filtrele: best ile çakışanları at
        remaining = []
        for det in sorted_dets:
            # Aynı sınıftan olmayanları atlama
            if len(det) >= 6 and len(best) >= 6 and det[5] != best[5]:
                remaining.append(det)
                continue
            if iou(best[:4], det[:4]) < iou_threshold:
                remaining.append(det)
        sorted_dets = remaining

    return kept


def extract_centers(detections) -> List[Tuple[float, float]]:
    """Detection listesinden merkez koordinatlarını çıkarır.

    Args:
        detections: Her biri [x1, y1, x2, y2, conf, cls] formatında.

    Returns:
        (cx, cy) tuple listesi.
    """
    centers = []
    for det in detections:
        if len(det) < 4:
            continue
        x1, y1, x2, y2 = det[:4]
        centers.append(((x1 + x2) / 2.0, (y1 + y2) / 2.0))
    return centers