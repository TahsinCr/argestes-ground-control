from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.core.events import EventStream
from src.models.video import (
    QrDetection,
    QrDetectionSnapshot,
    VideoFrame,
    VideoStreamStats,
)
from src.services.contracts.video import QrCodeService, VideoService
from src.services.logging_service import ServiceLogger


@dataclass(frozen=True, slots=True)
class _TrackedQr:
    track_id: str
    points: tuple[tuple[float, float], ...]
    data: str = ""
    decoded: bool = False
    submitted: bool = False
    missed_scans: int = 0

    @property
    def centroid(self) -> tuple[float, float]:
        count = max(1, len(self.points))
        return (
            sum(point[0] for point in self.points) / count,
            sum(point[1] for point in self.points) / count,
        )

    @property
    def diagonal(self) -> float:
        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        return math.hypot(max(xs) - min(xs), max(ys) - min(ys))

    def public(self) -> QrDetection:
        return QrDetection(
            track_id=self.track_id,
            points=self.points,
            decoded=self.decoded,
            data=self.data,
            submitted=self.submitted,
        )


class WeChatQrVideoService(VideoService, QrCodeService):
    """Video akışını WeChat QR ile tarar ve okunan metni canlı takip eder."""

    MODEL_FILES = (
        "detect_2021nov.prototxt",
        "detect_2021nov.caffemodel",
        "sr_2021nov.prototxt",
        "sr_2021nov.caffemodel",
    )

    def __init__(
        self,
        source: VideoService,
        model_dir: Path,
        logger: ServiceLogger | None = None,
        *,
        scan_interval_s: float = 0.12,
        max_missed_scans: int = 2,
        detector: Any | None = None,
    ) -> None:
        if scan_interval_s <= 0:
            raise ValueError("QR tarama aralığı pozitif olmalı")
        if max_missed_scans < 0:
            raise ValueError("QR kayıp toleransı negatif olamaz")
        self._source = source
        self._model_dir = Path(model_dir)
        self._logger = logger
        self._scan_interval_s = float(scan_interval_s)
        self._max_missed_scans = int(max_missed_scans)
        self._detector = detector
        self._frames = EventStream[VideoFrame]()
        self._qr_changed = EventStream[QrDetectionSnapshot]()
        self._qr_snapshot = QrDetectionSnapshot()
        self._tracks: dict[str, _TrackedQr] = {}
        self._submitted_qr_data: set[str] = set()
        self._rendered_qr_data: set[str] = set()
        self._session_qr_data = ""
        self._tracking_gray: Any | None = None
        self._tracking_timestamp: Any | None = None
        self._next_track = 1
        self._state_lock = threading.RLock()
        self._pending_condition = threading.Condition(threading.RLock())
        self._pending_frame: VideoFrame | None = None
        self._last_scan_submitted_at = 0.0
        self._running = False
        self._qr_active = False
        self._worker: threading.Thread | None = None
        self._last_processing_error = ""
        self._fallback_detector: Any | None = None
        self._wechat_fallback_announced = False
        self._source.frames.subscribe(self._on_source_frame)

    @property
    def connected(self) -> bool:
        return self._source.connected

    @property
    def connection_changed(self) -> EventStream[bool]:
        return self._source.connection_changed

    @property
    def frames(self) -> EventStream[VideoFrame]:
        return self._frames

    @property
    def stats(self) -> VideoStreamStats:
        return self._source.stats

    @property
    def stats_changed(self) -> EventStream[VideoStreamStats]:
        return self._source.stats_changed

    @property
    def qr_snapshot(self) -> QrDetectionSnapshot:
        with self._state_lock:
            return self._qr_snapshot

    @property
    def qr_changed(self) -> EventStream[QrDetectionSnapshot]:
        return self._qr_changed

    @property
    def qr_active(self) -> bool:
        with self._pending_condition:
            return self._qr_active

    def set_qr_active(self, active: bool) -> None:
        value = bool(active)
        with self._pending_condition:
            if value == self._qr_active:
                return
            self._qr_active = value
            self._pending_frame = None
            self._last_scan_submitted_at = 0.0
            self._pending_condition.notify_all()
        with self._state_lock:
            self._tracks.clear()
            self._submitted_qr_data.clear()
            self._rendered_qr_data.clear()
            self._session_qr_data = ""
            self._tracking_gray = None
            self._tracking_timestamp = None
            self._qr_snapshot = QrDetectionSnapshot()
            snapshot = self._qr_snapshot
        self._qr_changed.publish_isolated(snapshot)
        self._event(
            "qr.processing_activated" if value else "qr.processing_deactivated",
            (
                "Kamikaze modu için WeChat QR işleme etkinleştirildi"
                if value
                else "WeChat QR işleme durduruldu"
            ),
            severity="success" if value else "info",
        )

    def mark_qr_submitted(self, data: str) -> None:
        qr_data = data.strip()
        if not qr_data:
            raise ValueError("Gönderilmiş QR verisi boş olamaz")
        changed = False
        with self._state_lock:
            self._submitted_qr_data.add(qr_data)
            if qr_data in self._rendered_qr_data:
                changed = self._promote_submitted_tracks_locked(qr_data)
            snapshot = self._qr_snapshot
        if changed:
            self._qr_changed.publish_isolated(snapshot)

    def start(self) -> None:
        with self._pending_condition:
            if not self._running:
                self._running = True
                self._worker = threading.Thread(
                    target=self._process_frames,
                    name="WeChatQrProcessor",
                    daemon=True,
                )
                self._worker.start()
        self._source.start()

    def stop(self) -> None:
        self.set_qr_active(False)
        self._source.stop()
        with self._pending_condition:
            self._running = False
            self._pending_frame = None
            self._pending_condition.notify_all()
            worker = self._worker
            self._worker = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)

    def configure(
        self,
        *,
        source_type: str,
        source_uri: str,
        latency_ms: int,
        codec: str,
        frame_timeout_s: float,
    ) -> None:
        self._source.configure(
            source_type=source_type,
            source_uri=source_uri,
            latency_ms=latency_ms,
            codec=codec,
            frame_timeout_s=frame_timeout_s,
        )

    def reconnect(self) -> None:
        self._source.reconnect()

    def push(self, frame: VideoFrame) -> None:
        self._source.push(frame)

    def _on_source_frame(self, frame: VideoFrame) -> None:
        now = time.monotonic()
        if self.qr_active:
            try:
                self._track_qr_on_frame(frame)
            except Exception as exc:
                self._log_processing_error(exc)
        with self._pending_condition:
            if (
                self._running
                and self._qr_active
                and now - self._last_scan_submitted_at >= self._scan_interval_s
            ):
                self._pending_frame = frame
                self._last_scan_submitted_at = now
                self._pending_condition.notify()
        try:
            annotated = self._annotate_frame(frame, self.qr_snapshot.detections)
        except Exception as exc:
            self._log_processing_error(exc)
            annotated = frame
        self._frames.publish_isolated(annotated)

    def _process_frames(self) -> None:
        detector = self._detector
        announced = False
        while True:
            with self._pending_condition:
                while self._running and (
                    not self._qr_active or self._pending_frame is None
                ):
                    self._pending_condition.wait(timeout=0.5)
                if not self._running:
                    return
                frame = self._pending_frame
                self._pending_frame = None
            if frame is None:
                continue
            try:
                if detector is None:
                    detector = self._create_detector()
                if not announced:
                    announced = True
                    self._event(
                        "qr.processor_started",
                        "WeChat QR video işleyicisi hazır",
                        severity="success",
                        details={"model_dir": str(self._model_dir)},
                    )
                observations = self._detect(detector, frame)
                with self._pending_condition:
                    if not self._qr_active:
                        continue
                observations = self._align_observations_to_live_frame(
                    frame,
                    observations,
                )
                self._update_tracks(observations)
                self._last_processing_error = ""
            except Exception as exc:
                self._log_processing_error(exc, unavailable=detector is None)

    def _create_detector(self) -> Any:
        import cv2

        paths = tuple(self._model_dir / name for name in self.MODEL_FILES)
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "WeChat QR model dosyaları bulunamadı: " + ", ".join(missing)
            )
        return cv2.wechat_qrcode_WeChatQRCode(*(str(path) for path in paths))

    def _detect(
        self,
        detector: Any,
        frame: VideoFrame,
    ) -> list[tuple[str, tuple[tuple[float, float], ...]]]:
        import cv2
        import numpy as np

        image = WeChatQrVideoService._frame_array(frame, copy=False)
        if frame.pixel_format == "rgba8888":
            detector_image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
        elif frame.pixel_format == "rgb888":
            detector_image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        else:
            detector_image = np.ascontiguousarray(image)
        detector_image = np.ascontiguousarray(detector_image)

        # WeChat super-resolution adimi, goruntunun kenarina cok yakin QR'larda
        # OpenCV icinde negatif ROI uretebiliyor. Gercek WeChat detectorune
        # guvenli bir beyaz kenar ekleyip donen koordinatlari geri tasiyoruz.
        is_wechat = "wechat" in detector.__class__.__name__.lower()
        padding = max(32, round(min(frame.width, frame.height) * 0.04)) if is_wechat else 0
        detector_input = (
            cv2.copyMakeBorder(
                detector_image,
                padding,
                padding,
                padding,
                padding,
                cv2.BORDER_CONSTANT,
                value=255 if detector_image.ndim == 2 else (255, 255, 255),
            )
            if padding
            else detector_image
        )
        try:
            decoded_values, raw_points = detector.detectAndDecode(detector_input)
        except cv2.error as exc:
            observations = self._detect_with_standard_qr(detector_image)
            if not self._wechat_fallback_announced:
                self._wechat_fallback_announced = True
                self._event(
                    "qr.wechat_fallback",
                    "WeChat QR kenar ROI hatasi standart OpenCV okuyucusuyla telafi edildi",
                    severity="warning",
                    details={"opencv_error": str(exc)},
                )
            return observations
        texts = [str(value) for value in decoded_values] if decoded_values is not None else []
        polygons: list[tuple[tuple[float, float], ...]] = []
        if raw_points is not None:
            if isinstance(raw_points, np.ndarray) and raw_points.ndim == 2:
                point_sets = [raw_points]
            else:
                point_sets = list(raw_points)
            for raw_polygon in point_sets:
                polygon_array = np.asarray(raw_polygon, dtype=np.float32).reshape(-1, 2)
                if len(polygon_array) < 4:
                    continue
                polygon_array[:, 0] = np.clip(
                    polygon_array[:, 0] - padding,
                    0.0,
                    max(0.0, frame.width - 1.0),
                )
                polygon_array[:, 1] = np.clip(
                    polygon_array[:, 1] - padding,
                    0.0,
                    max(0.0, frame.height - 1.0),
                )
                if not np.all(np.isfinite(polygon_array)):
                    continue
                if cv2.contourArea(polygon_array) < 16.0:
                    continue
                polygons.append(
                    tuple((float(point[0]), float(point[1])) for point in polygon_array)
                )
        observations = [
            (texts[index].strip() if index < len(texts) else "", polygon)
            for index, polygon in enumerate(polygons)
        ]
        if observations:
            return observations
        return self._detect_with_standard_qr(detector_image)

    def _detect_with_standard_qr(
        self,
        image: Any,
    ) -> list[tuple[str, tuple[tuple[float, float], ...]]]:
        """WeChat'in ROI sinir hatasinda OpenCV'nin yerlesik okuyucusunu kullan."""
        import cv2
        import numpy as np

        if self._fallback_detector is None:
            self._fallback_detector = cv2.QRCodeDetector()
        detector = self._fallback_detector
        observations: list[tuple[str, tuple[tuple[float, float], ...]]] = []
        try:
            detected, decoded_values, raw_points, _straight = (
                detector.detectAndDecodeMulti(image)
            )
        except (AttributeError, cv2.error):
            detected = False
            decoded_values = ()
            raw_points = None
        if detected and raw_points is not None:
            for index, raw_polygon in enumerate(raw_points):
                polygon = np.asarray(raw_polygon, dtype=np.float32).reshape(-1, 2)
                if len(polygon) < 4 or not np.all(np.isfinite(polygon)):
                    continue
                data = (
                    str(decoded_values[index]).strip()
                    if index < len(decoded_values)
                    else ""
                )
                observations.append(
                    (data, tuple((float(x), float(y)) for x, y in polygon))
                )
        if observations:
            return observations
        try:
            data, raw_points, _straight = detector.detectAndDecode(image)
        except cv2.error:
            return []
        if raw_points is None:
            return []
        polygon = np.asarray(raw_points, dtype=np.float32).reshape(-1, 2)
        if len(polygon) < 4 or not np.all(np.isfinite(polygon)):
            return []
        return [
            (
                str(data).strip(),
                tuple((float(x), float(y)) for x, y in polygon),
            )
        ]

    def _update_tracks(
        self,
        observations: list[tuple[str, tuple[tuple[float, float], ...]]],
    ) -> None:
        transitions: list[tuple[str, _TrackedQr]] = []
        with self._state_lock:
            previous = dict(self._tracks)
            observations = self._select_single_observation_locked(
                observations,
                previous,
            )
            unmatched = set(previous)
            updated: dict[str, _TrackedQr] = {}
            for data, points in observations:
                matched_id = self._match_track(points, previous, unmatched)
                if matched_id is None:
                    matched_id = f"qr-{self._next_track}"
                    self._next_track += 1
                    old = None
                else:
                    old = previous[matched_id]
                    unmatched.discard(matched_id)
                decoded = bool(data) or bool(old and old.decoded)
                retained_data = data or (old.data if old else "")
                submitted = bool(
                    retained_data and retained_data in self._submitted_qr_data
                )
                track = _TrackedQr(
                    matched_id,
                    points,
                    retained_data,
                    decoded,
                    submitted,
                    0,
                )
                updated[matched_id] = track
                if old is None:
                    if not submitted:
                        transitions.append(("decoded" if decoded else "detected", track))
                elif not old.decoded and decoded:
                    transitions.append(("decoded", track))
            for track_id in unmatched:
                old = previous[track_id]
                # Optik akis yalnizca iki WeChat taramasi arasinda koordinati
                # yumusatir. Algilayicinin bos sonucu, QR'in goruntuden
                # ciktigina dair asil kanittir ve kayip sayacini ilerletir.
                missed = old.missed_scans + 1
                if missed <= self._max_missed_scans:
                    updated[track_id] = replace(old, missed_scans=missed)
                else:
                    transitions.append(("lost", old))
            self._tracks = updated
            snapshot = QrDetectionSnapshot(
                tuple(track.public() for track in updated.values())
            )
            self._qr_snapshot = snapshot
        self._qr_changed.publish_isolated(snapshot)
        for transition, track in transitions:
            self._log_transition(transition, track)

    def _select_single_observation_locked(
        self,
        observations: list[tuple[str, tuple[tuple[float, float], ...]]],
        previous: dict[str, _TrackedQr],
    ) -> list[tuple[str, tuple[tuple[float, float], ...]]]:
        """Kamikaze oturumunda yalnizca ilk cozumlenen QR'i takip et."""
        candidates = [
            (str(data).strip(), tuple(points))
            for data, points in observations
            if len(points) >= 4
        ]
        if not candidates:
            return []

        previous_track = next(iter(previous.values()), None)
        if self._session_qr_data:
            same_data = [
                observation
                for observation in candidates
                if observation[0] == self._session_qr_data
            ]
            if same_data:
                return [self._largest_observation(same_data)]
            if previous_track is not None:
                spatial_blanks = [
                    observation
                    for observation in candidates
                    if not observation[0]
                    and self._match_track(
                        observation[1],
                        previous,
                        {previous_track.track_id},
                    )
                    == previous_track.track_id
                ]
                if spatial_blanks:
                    return [self._largest_observation(spatial_blanks)]
            # Ilk okunan QR disindaki kodlar bu gorev oturumunda yok sayilir.
            return []

        if previous_track is not None:
            spatial_matches = [
                observation
                for observation in candidates
                if self._match_track(
                    observation[1],
                    previous,
                    {previous_track.track_id},
                )
                == previous_track.track_id
            ]
            if spatial_matches:
                candidates = spatial_matches

        # Birden fazla sonuc donerse cozumlenmis ve daha buyuk olan tek QR'i sec.
        selected = max(
            candidates,
            key=lambda observation: (
                bool(observation[0]),
                self._polygon_area(observation[1]),
            ),
        )
        if selected[0]:
            self._session_qr_data = selected[0]
        return [selected]

    @classmethod
    def _largest_observation(
        cls,
        observations: list[tuple[str, tuple[tuple[float, float], ...]]],
    ) -> tuple[str, tuple[tuple[float, float], ...]]:
        return max(observations, key=lambda item: cls._polygon_area(item[1]))

    @staticmethod
    def _polygon_area(points: tuple[tuple[float, float], ...]) -> float:
        return abs(
            sum(
                x1 * y2 - x2 * y1
                for (x1, y1), (x2, y2) in zip(
                    points,
                    points[1:] + points[:1],
                )
            )
        ) / 2.0

    def _track_qr_on_frame(self, frame: VideoFrame) -> None:
        """WeChat taramaları arasında QR köşelerini canlı kare üzerinde taşır."""
        import numpy as np

        current_gray = self._gray_frame(frame)
        with self._state_lock:
            previous_gray = self._tracking_gray
            previous_tracks = dict(self._tracks)
            self._tracking_gray = current_gray
            self._tracking_timestamp = frame.timestamp
        if (
            previous_gray is None
            or not previous_tracks
            or previous_gray.shape != current_gray.shape
        ):
            return

        height, width = current_gray.shape[:2]
        tracked_points: dict[str, tuple[tuple[float, float], ...]] = {}
        for track_id, track in previous_tracks.items():
            points = self._track_polygon_between_frames(
                previous_gray,
                current_gray,
                track.points,
            )
            if points is None:
                continue
            candidate = np.asarray(points, dtype=np.float32)
            if not bool(
                np.all(candidate[:, 0] >= -4.0)
                and np.all(candidate[:, 0] <= width + 4.0)
                and np.all(candidate[:, 1] >= -4.0)
                and np.all(candidate[:, 1] <= height + 4.0)
            ):
                continue
            tracked_points[track_id] = points
        if not tracked_points:
            return

        with self._state_lock:
            changed = False
            updated = dict(self._tracks)
            for track_id, points in tracked_points.items():
                current = updated.get(track_id)
                original = previous_tracks[track_id]
                if current is None or current.points != original.points:
                    continue
                updated[track_id] = replace(
                    current,
                    points=points,
                )
                changed = True
            if changed:
                self._tracks = updated
                self._qr_snapshot = QrDetectionSnapshot(
                    tuple(track.public() for track in updated.values())
                )

    def _align_observations_to_live_frame(
        self,
        detector_frame: VideoFrame,
        observations: list[tuple[str, tuple[tuple[float, float], ...]]],
    ) -> list[tuple[str, tuple[tuple[float, float], ...]]]:
        """Arka plan taramasının eski koordinatlarını en yeni video karesine taşır."""
        if not observations:
            return observations
        with self._state_lock:
            live_gray = self._tracking_gray
            live_timestamp = self._tracking_timestamp
        if live_gray is None or live_timestamp == detector_frame.timestamp:
            return observations

        detector_gray = self._gray_frame(detector_frame)
        if detector_gray.shape != live_gray.shape:
            return observations
        aligned: list[tuple[str, tuple[tuple[float, float], ...]]] = []
        for data, original in observations:
            points = self._track_polygon_between_frames(
                detector_gray,
                live_gray,
                original,
            )
            aligned.append((data, points or original))
        return aligned

    @staticmethod
    def _track_polygon_between_frames(
        previous_gray: Any,
        current_gray: Any,
        polygon: tuple[tuple[float, float], ...],
    ) -> tuple[tuple[float, float], ...] | None:
        """QR iç özelliklerinden homografi çıkararak dört köşeyi yeni kareye taşır."""
        import cv2
        import numpy as np

        polygon_global = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
        height, width = previous_gray.shape[:2]
        span_x = float(np.ptp(polygon_global[:, 0]))
        span_y = float(np.ptp(polygon_global[:, 1]))
        margin = max(40.0, max(span_x, span_y) * 0.55)
        left = max(0, int(math.floor(float(np.min(polygon_global[:, 0])) - margin)))
        top = max(0, int(math.floor(float(np.min(polygon_global[:, 1])) - margin)))
        right = min(width, int(math.ceil(float(np.max(polygon_global[:, 0])) + margin)))
        bottom = min(height, int(math.ceil(float(np.max(polygon_global[:, 1])) + margin)))
        if right - left < 8 or bottom - top < 8:
            return None
        previous_roi = previous_gray[top:bottom, left:right]
        current_roi = current_gray[top:bottom, left:right]
        offset = np.asarray((left, top), dtype=np.float32)
        polygon_array = (polygon_global - offset).reshape(-1, 1, 2)
        mask = np.zeros(previous_roi.shape[:2], dtype=np.uint8)
        cv2.fillConvexPoly(
            mask,
            np.rint(polygon_array.reshape(-1, 2)).astype(np.int32),
            255,
        )
        features = cv2.goodFeaturesToTrack(
            previous_roi,
            maxCorners=48,
            qualityLevel=0.01,
            minDistance=4.0,
            mask=mask,
            blockSize=5,
        )
        if features is None or len(features) < 4:
            features = polygon_array
        initial_points = None
        flow_flags = 0
        template_left = max(0, int(math.floor(float(np.min(polygon_array[:, 0, 0])))))
        template_top = max(0, int(math.floor(float(np.min(polygon_array[:, 0, 1])))))
        template_right = min(
            previous_roi.shape[1],
            int(math.ceil(float(np.max(polygon_array[:, 0, 0])))) + 1,
        )
        template_bottom = min(
            previous_roi.shape[0],
            int(math.ceil(float(np.max(polygon_array[:, 0, 1])))) + 1,
        )
        template = previous_roi[
            template_top:template_bottom,
            template_left:template_right,
        ]
        if (
            template.size > 0
            and template.shape[0] <= current_roi.shape[0]
            and template.shape[1] <= current_roi.shape[1]
            and float(np.std(template)) > 1.0
        ):
            match = cv2.matchTemplate(
                current_roi,
                template,
                cv2.TM_CCOEFF_NORMED,
            )
            _minimum, score, _minimum_at, matched_at = cv2.minMaxLoc(match)
            if score >= 0.35:
                translation = np.asarray(
                    (
                        float(matched_at[0] - template_left),
                        float(matched_at[1] - template_top),
                    ),
                    dtype=np.float32,
                )
                initial_points = features + translation.reshape(1, 1, 2)
                flow_flags = cv2.OPTFLOW_USE_INITIAL_FLOW
        tracked, statuses, _errors = cv2.calcOpticalFlowPyrLK(
            previous_roi,
            current_roi,
            features,
            initial_points,
            winSize=(31, 31),
            maxLevel=3,
            criteria=(
                cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                24,
                0.01,
            ),
            flags=flow_flags,
        )
        if tracked is None or statuses is None:
            return None
        returned, returned_statuses, _return_errors = cv2.calcOpticalFlowPyrLK(
            current_roi,
            previous_roi,
            tracked,
            None,
            winSize=(31, 31),
            maxLevel=3,
            criteria=(
                cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                24,
                0.01,
            ),
        )
        if returned is None or returned_statuses is None:
            return None
        return_error = np.linalg.norm(
            returned.reshape(-1, 2) - features.reshape(-1, 2),
            axis=1,
        )
        valid = (
            statuses.reshape(-1).astype(bool)
            & returned_statuses.reshape(-1).astype(bool)
            & (return_error <= 2.0)
        )
        source = features.reshape(-1, 2)[valid]
        target = tracked.reshape(-1, 2)[valid]
        if len(source) >= 4:
            transform, _inliers = cv2.findHomography(
                source,
                target,
                cv2.RANSAC,
                3.0,
            )
            if transform is not None:
                moved = cv2.perspectiveTransform(polygon_array, transform)
                return tuple(
                    (float(point[0] + left), float(point[1] + top))
                    for point in moved.reshape(-1, 2)
                )
        if len(source) >= 2:
            transform, _inliers = cv2.estimateAffinePartial2D(
                source,
                target,
                method=cv2.RANSAC,
                ransacReprojThreshold=3.0,
            )
            if transform is not None:
                moved = cv2.transform(polygon_array, transform)
                return tuple(
                    (float(point[0] + left), float(point[1] + top))
                    for point in moved.reshape(-1, 2)
                )
        return None

    @staticmethod
    def _gray_frame(frame: VideoFrame) -> Any:
        import cv2

        image = WeChatQrVideoService._frame_array(frame, copy=False)
        if frame.pixel_format == "rgba8888":
            return cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
        if frame.pixel_format == "rgb888":
            return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        if frame.pixel_format == "bgr888":
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return image.copy()

    @staticmethod
    def _match_track(
        points: tuple[tuple[float, float], ...],
        tracks: dict[str, _TrackedQr],
        candidates: set[str],
    ) -> str | None:
        count = max(1, len(points))
        centroid = (
            sum(point[0] for point in points) / count,
            sum(point[1] for point in points) / count,
        )
        best_id: str | None = None
        best_distance = float("inf")
        for track_id in candidates:
            track = tracks[track_id]
            distance = math.dist(centroid, track.centroid)
            threshold = max(45.0, track.diagonal * 0.85)
            if distance <= threshold and distance < best_distance:
                best_id = track_id
                best_distance = distance
        return best_id

    @staticmethod
    def _frame_array(frame: VideoFrame, *, copy: bool) -> Any:
        import numpy as np

        channels = {"rgba8888": 4, "rgb888": 3, "bgr888": 3, "gray8": 1}[
            frame.pixel_format
        ]
        stride = frame.stride or frame.width * channels
        rows = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, stride)
        pixels = rows[:, : frame.width * channels]
        shape = (
            (frame.height, frame.width)
            if channels == 1
            else (frame.height, frame.width, channels)
        )
        array = pixels.reshape(shape)
        return array.copy() if copy else array

    def _annotate_frame(
        self,
        frame: VideoFrame,
        detections: tuple[QrDetection, ...],
    ) -> VideoFrame:
        decoded_detections = tuple(
            detection
            for detection in detections
            if detection.decoded and detection.data.strip()
        )
        if not decoded_detections:
            return frame
        import cv2

        canvas = WeChatQrVideoService._frame_array(frame, copy=True)
        channels = 1 if canvas.ndim == 2 else canvas.shape[2]
        if channels == 1:
            canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
            pixel_format = "bgr888"
            channels = 3
        else:
            pixel_format = frame.pixel_format
        thickness = max(1, round(min(frame.width, frame.height) / 360))
        font_scale = max(0.5, min(1.0, min(frame.width, frame.height) / 720.0))
        for detection in decoded_detections:
            if pixel_format == "bgr888":
                color = (68, 214, 96)
                outline = (4, 10, 4)
            else:
                color = (96, 214, 68, 255)
                outline = (4, 10, 4, 255)
                if channels == 3:
                    color = color[:3]
                    outline = outline[:3]
            minimum_x = int(min(point[0] for point in detection.points))
            minimum_y = int(min(point[1] for point in detection.points))
            compact_data = " ".join(detection.data.split())
            label = compact_data[:64]
            (text_width, text_height), baseline = cv2.getTextSize(
                label,
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                thickness,
            )
            text_x = max(4, min(frame.width - text_width - 4, minimum_x))
            text_y = max(
                text_height + baseline + 4,
                min(frame.height - baseline - 4, minimum_y - 6),
            )
            cv2.putText(
                canvas,
                label,
                (text_x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                outline,
                min(3, thickness + 2),
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                label,
                (text_x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                color,
                thickness,
                cv2.LINE_AA,
            )
        annotated = VideoFrame(
            width=frame.width,
            height=frame.height,
            data=canvas.tobytes(),
            pixel_format=pixel_format,
            stride=frame.width * channels,
            timestamp=frame.timestamp,
        )
        self._record_decoded_rendered(decoded_detections)
        return annotated

    def _record_decoded_rendered(self, detections: tuple[QrDetection, ...]) -> None:
        data_values = {
            detection.data.strip()
            for detection in detections
            if detection.decoded and not detection.submitted and detection.data.strip()
        }
        if not data_values:
            return
        changed = False
        with self._state_lock:
            self._rendered_qr_data.update(data_values)
            for data in data_values & self._submitted_qr_data:
                changed = self._promote_submitted_tracks_locked(data) or changed
            snapshot = self._qr_snapshot
        if changed:
            self._qr_changed.publish_isolated(snapshot)

    def _promote_submitted_tracks_locked(self, data: str) -> bool:
        changed = False
        updated: dict[str, _TrackedQr] = {}
        for track_id, track in self._tracks.items():
            if track.decoded and track.data == data and not track.submitted:
                track = replace(track, submitted=True)
                changed = True
            updated[track_id] = track
        if changed:
            self._tracks = updated
            self._qr_snapshot = QrDetectionSnapshot(
                tuple(track.public() for track in updated.values())
            )
        return changed

    def _log_transition(self, transition: str, track: _TrackedQr) -> None:
        if transition == "decoded":
            self._event(
                "qr.decoded",
                f"QR verisi okundu: {track.data}",
                severity="success",
                details={"track_id": track.track_id, "data": track.data},
            )
        elif transition == "detected":
            self._event(
                "qr.detected",
                "QR alanı algılandı; veri çözümü bekleniyor",
                severity="warning",
                details={"track_id": track.track_id},
            )
        else:
            self._event(
                "qr.lost",
                "QR alanı kamera görüntüsünden çıktı",
                details={"track_id": track.track_id, "decoded": track.decoded},
            )

    def _log_processing_error(self, error: Exception, *, unavailable: bool = False) -> None:
        signature = f"{error.__class__.__name__}:{error}"
        if signature == self._last_processing_error:
            return
        self._last_processing_error = signature
        self._event(
            "qr.processor_unavailable" if unavailable else "qr.processing_failed",
            f"QR işleme hatası: {error}",
            severity="danger",
        )

    def _event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(
                action,
                message,
                severity=severity,
                details=details or {},
            )
