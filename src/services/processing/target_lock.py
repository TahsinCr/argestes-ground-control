from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from target_tracker.lock_logic.lock_timer import LockTimer
from target_tracker.lock_logic.vurus_alani import VurusAlani
from target_tracker.perception.yolo_infer import YoloInfer
from target_tracker.tracking.bytetrack_wrapper import ByteTrackWrapper
from target_tracker.tracking.ekf import ExtendedKalmanFilter

from src.core.events import EventStream, Subscription
from src.models.target_lock import (
    TargetBoundingBox,
    TargetLockAcquisition,
    TargetLockVisionState,
)
from src.models.telemetry import TargetTrack, TelemetrySnapshot
from src.models.video import VideoFrame, VideoStreamStats
from src.services.contracts.target_lock import TargetLockVisionService
from src.services.contracts.telemetry import TelemetryService
from src.services.contracts.video import VideoService
from src.services.logging_service import ServiceLogger


class TargetLockVideoService(VideoService, TargetLockVisionService):
    """Video akışında YOLO + ByteTrack + EKF ile şartname uyumlu hedef takibi."""

    AIRPLANE_CLASS_ID = 4
    VEHICLE_SOURCE = "opencv-target-lock-simulation"

    def __init__(
        self,
        source: VideoService,
        model_path: Path,
        telemetry: TelemetryService,
        logger: ServiceLogger | None = None,
        *,
        detector: Any | None = None,
        inference_interval_s: float = 0.06,
        lock_duration_s: float = 4.0,
        feedback_duration_s: float = 1.0,
    ) -> None:
        if inference_interval_s <= 0:
            raise ValueError("Hedef çıkarım aralığı pozitif olmalı")
        if feedback_duration_s <= 0:
            raise ValueError("Kilit geri bildirim süresi pozitif olmalı")
        self._source = source
        self._model_path = Path(model_path)
        self._telemetry = telemetry
        self._logger = logger
        self._detector = detector or YoloInfer(
            str(self._model_path),
            input_shape=(640, 640),
            conf_threshold=0.30,
        )
        self._inference_interval_s = float(inference_interval_s)
        self._feedback_duration_s = float(feedback_duration_s)
        self._tracker = ByteTrackWrapper(
            track_high_thresh=0.35,
            track_low_thresh=0.10,
            match_thresh=0.30,
            new_track_thresh=0.35,
            max_lost_frames=10,
            min_hit_streak=2,
        )
        self._ekf = ExtendedKalmanFilter()
        self._lock_timer = LockTimer(
            lock_duration=lock_duration_s,
            tolerance=1.0,
        )
        self._frames = EventStream[VideoFrame]()
        self._changed = EventStream[TargetLockVisionState]()
        self._acquired = EventStream[TargetLockAcquisition]()
        self._state = TargetLockVisionState()
        self._state_lock = threading.RLock()
        self._processing_lock = threading.RLock()
        self._pending_condition = threading.Condition(threading.RLock())
        self._pending_frame: tuple[int, VideoFrame] | None = None
        self._generation = 0
        self._last_frame_queued_at = 0.0
        self._last_inference_at = 0.0
        self._running = False
        self._active = False
        self._acquisition_emitted = False
        self._current_target_id: int | None = None
        self._worker: threading.Thread | None = None
        self._telemetry_subscription: Subscription | None = None
        self._server_anchor: datetime | None = None
        self._server_anchor_monotonic = 0.0
        self._last_server_sample: datetime | None = None
        self._last_error_signature = ""
        self._feedback = ""
        self._feedback_until_monotonic = 0.0
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
    def target_lock_state(self) -> TargetLockVisionState:
        with self._state_lock:
            return replace(self._state)

    @property
    def target_lock_changed(self) -> EventStream[TargetLockVisionState]:
        return self._changed

    @property
    def target_acquired(self) -> EventStream[TargetLockAcquisition]:
        return self._acquired

    def start(self) -> None:
        with self._pending_condition:
            if not self._running:
                self._running = True
                self._worker = threading.Thread(
                    target=self._process_frames,
                    name="TargetLockProcessor",
                    daemon=True,
                )
                self._worker.start()
        if self._telemetry_subscription is None:
            self._telemetry_subscription = self._telemetry.updated.subscribe(
                self._on_telemetry
            )
        self._on_telemetry(self._telemetry.current)
        self._source.start()

    def stop(self) -> None:
        self.set_target_lock_active(False)
        self._source.stop()
        with self._pending_condition:
            self._running = False
            self._pending_frame = None
            self._pending_condition.notify_all()
            worker = self._worker
            self._worker = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3.0)
        if self._telemetry_subscription is not None:
            self._telemetry_subscription.cancel()
            self._telemetry_subscription = None

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

    def set_target_lock_active(self, active: bool) -> None:
        value = bool(active)
        with self._pending_condition:
            if value == self._active:
                return
            # Yavas bir inference sonucu gorev kapandiktan veya yeni bir gorev
            # oturumu basladiktan sonra duruma sizmasin.
            self._generation += 1
            self._active = value
            self._pending_frame = None
            self._last_frame_queued_at = 0.0
            self._pending_condition.notify_all()
        with self._processing_lock:
            self._tracker.reset()
            self._ekf.reset()
            self._lock_timer.reset()
            self._last_inference_at = 0.0
            self._current_target_id = None
            self._acquisition_emitted = False
            self._feedback = ""
            self._feedback_until_monotonic = 0.0
        state = TargetLockVisionState(
            active=value,
            phase="ACQUIRE" if value else "IDLE",
            model_ready=bool(getattr(self._detector, "ready", False)),
            model_backend=str(getattr(self._detector, "backend", "")),
        )
        self._publish_state(state)
        self._publish_target_track(None, locked=False)
        self._event(
            "target_lock.processing_activated"
            if value
            else "target_lock.processing_deactivated",
            "Target Lock görüntü işleme etkinleştirildi"
            if value
            else "Target Lock görüntü işleme durduruldu",
            severity="success" if value else "info",
            details={"model": str(self._model_path)},
        )

    def _on_source_frame(self, frame: VideoFrame) -> None:
        now = time.monotonic()
        self._expire_feedback(now)
        with self._pending_condition:
            if (
                self._running
                and self._active
                and now - self._last_frame_queued_at >= self._inference_interval_s
            ):
                self._pending_frame = (self._generation, frame)
                self._last_frame_queued_at = now
                self._pending_condition.notify()
        try:
            annotated = self._annotate_frame(frame, self.target_lock_state)
        except Exception as exc:
            self._log_error("target_lock.annotation_failed", exc)
            annotated = frame
        self._frames.publish_isolated(annotated)

    def _process_frames(self) -> None:
        while True:
            with self._pending_condition:
                while self._running and (
                    not self._active or self._pending_frame is None
                ):
                    self._pending_condition.wait(timeout=0.5)
                if not self._running:
                    return
                pending = self._pending_frame
                self._pending_frame = None
            if pending is None:
                continue
            generation, frame = pending
            try:
                image = self._bgr_frame(frame)
                detections = [
                    detection
                    for detection in self._detector.infer(image)
                    if len(detection) >= 6
                    and int(detection[5]) == self.AIRPLANE_CLASS_ID
                ]
                with self._pending_condition:
                    result_is_current = (
                        self._running
                        and self._active
                        and generation == self._generation
                    )
                if not result_is_current:
                    continue
                self._consume_detections(frame, detections, time.monotonic())
                if getattr(self._detector, "ready", False):
                    self._last_error_signature = ""
                elif getattr(self._detector, "last_error", ""):
                    raise RuntimeError(str(self._detector.last_error))
            except Exception as exc:
                self._log_error("target_lock.processing_failed", exc)
                with self._state_lock:
                    state = replace(
                        self._state,
                        model_ready=False,
                        last_error=str(exc) or exc.__class__.__name__,
                        updated_at=datetime.now(UTC),
                    )
                self._publish_state(state)

    def _consume_detections(
        self,
        frame: VideoFrame,
        detections: list[Any],
        now_monotonic: float,
    ) -> None:
        with self._processing_lock:
            tracked = self._tracker.update(detections)
            target = self._select_target(tracked)
            dt = (
                0.0
                if self._last_inference_at <= 0.0
                else max(0.0, now_monotonic - self._last_inference_at)
            )
            self._last_inference_at = now_monotonic
            zone = VurusAlani(frame.width, frame.height)
            target_box: TargetBoundingBox | None = None
            in_zone = False
            if target is not None:
                x1, y1, x2, y2 = (float(value) for value in target[:4])
                confidence = float(target[4])
                track_id = int(target[6])
                width = max(2.0, x2 - x1)
                height = max(2.0, y2 - y1)
                center_x = x1 + width / 2.0
                center_y = y1 + height / 2.0
                self._ekf.dt = max(dt, self._inference_interval_s)
                self._ekf.predict()
                filtered = self._ekf.update(
                    (center_x, center_y, width, height),
                    confidence=confidence,
                )
                if filtered is not None:
                    center_x, center_y, width, height = filtered
                x1 = max(0.0, min(frame.width - 2.0, center_x - width / 2.0))
                y1 = max(0.0, min(frame.height - 2.0, center_y - height / 2.0))
                width = max(2.0, min(width, frame.width - x1))
                height = max(2.0, min(height, frame.height - y1))
                target_box = TargetBoundingBox(
                    x=round(x1),
                    y=round(y1),
                    width=round(width),
                    height=round(height),
                    confidence=max(0.0, min(1.0, confidence)),
                    track_id=track_id,
                )
                in_zone = zone.is_inside(
                    target_box.center_x,
                    target_box.center_y,
                    target_box.width,
                    target_box.height,
                )
            was_locked = self._lock_timer.locked
            locked = self._lock_timer.update(dt, bool(target_box and in_zone))
            if locked and not was_locked:
                self._feedback = "success"
                self._feedback_until_monotonic = (
                    now_monotonic + self._feedback_duration_s
                )
            elif self._lock_timer.just_failed:
                self._feedback = "failure"
                self._feedback_until_monotonic = (
                    now_monotonic + self._feedback_duration_s
                )
            elif now_monotonic >= self._feedback_until_monotonic:
                self._feedback = ""
            if locked:
                phase = "LOCKED"
            elif target_box is None:
                phase = "ACQUIRE"
            elif in_zone:
                phase = "LOCKING"
            else:
                phase = "TRACK"
            state = TargetLockVisionState(
                active=True,
                phase=phase,
                target=target_box,
                strike_zone=zone.bounds,
                in_strike_zone=in_zone,
                progress=self._lock_timer.current_progress,
                lock_elapsed_s=self._lock_timer.elapsed,
                window_elapsed_s=self._lock_timer.window_elapsed,
                lock_required_s=self._lock_timer.lock_duration,
                window_duration_s=(
                    self._lock_timer.lock_duration + self._lock_timer.tolerance
                ),
                locked=locked,
                feedback=self._feedback,
                model_ready=bool(getattr(self._detector, "ready", True)),
                model_backend=str(getattr(self._detector, "backend", "test")),
                last_error="",
            )
            acquisition = None
            if locked and target_box is not None and not self._acquisition_emitted:
                self._acquisition_emitted = True
                acquisition = TargetLockAcquisition(
                    ended_at=self._server_now(),
                    target=target_box,
                    autonomous=True,
                )
            failed = self._lock_timer.just_failed
        self._publish_state(state)
        self._publish_target_track(target_box, locked=locked)
        if failed:
            self._event(
                "target_lock.failed",
                "Hedef, belirlenen kilit süresi ve toleransı içinde tutulamadı",
                severity="warning",
                details={
                    "feedback_duration_s": self._feedback_duration_s,
                    "valid_lock_s": round(
                        self._lock_timer.last_attempt_elapsed,
                        3,
                    ),
                    "window_s": round(
                        self._lock_timer.last_attempt_window_elapsed,
                        3,
                    ),
                    "out_of_zone_s": round(
                        self._lock_timer.last_attempt_invalid_elapsed,
                        3,
                    ),
                },
            )
        if acquisition is not None:
            self._event(
                "target_lock.acquired",
                "Hedef 4 saniye boyunca şartname vuruş alanında tutuldu",
                severity="success",
                details={
                    "track_id": acquisition.target.track_id,
                    "ended_at": acquisition.ended_at.isoformat(),
                },
            )
            self._acquired.publish_isolated(acquisition)

    def _select_target(self, tracked: list[Any]) -> list[Any] | None:
        if not tracked:
            return None
        if self._current_target_id is not None:
            for detection in tracked:
                if len(detection) >= 7 and int(detection[6]) == self._current_target_id:
                    return list(detection)
        best = max(tracked, key=lambda item: float(item[4]))
        next_target_id = int(best[6])
        if (
            self._current_target_id is not None
            and next_target_id != self._current_target_id
        ):
            self._ekf.reset()
        self._current_target_id = next_target_id
        return list(best)

    def _publish_state(self, state: TargetLockVisionState) -> None:
        with self._state_lock:
            self._state = state
        self._changed.publish_isolated(replace(state))

    def _expire_feedback(self, now_monotonic: float) -> None:
        with self._processing_lock:
            if not self._feedback or now_monotonic < self._feedback_until_monotonic:
                return
            self._feedback = ""
            self._feedback_until_monotonic = 0.0
        with self._state_lock:
            if not self._state.feedback:
                return
            state = replace(
                self._state,
                feedback="",
                updated_at=datetime.now(UTC),
            )
        self._publish_state(state)

    def _publish_target_track(
        self,
        target: TargetBoundingBox | None,
        *,
        locked: bool,
    ) -> None:
        value = (
            TargetTrack()
            if target is None
            else TargetTrack(
                locked=locked,
                center_x=target.center_x,
                center_y=target.center_y,
                width=target.width,
                height=target.height,
                vehicle_source=self.VEHICLE_SOURCE,
            )
        )
        if self._telemetry.current.target_track != value:
            self._telemetry.patch(target_track=value)

    def _on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        server_time = snapshot.server_time
        if not isinstance(server_time, datetime) or server_time.tzinfo is None:
            return
        sample = server_time.astimezone(UTC)
        with self._state_lock:
            if sample == self._last_server_sample:
                return
            self._last_server_sample = sample
            self._server_anchor = sample
            self._server_anchor_monotonic = time.monotonic()

    def _server_now(self) -> datetime:
        with self._state_lock:
            anchor = self._server_anchor
            elapsed = time.monotonic() - self._server_anchor_monotonic
        if anchor is None:
            return datetime.now(UTC)
        return anchor + timedelta(seconds=max(0.0, elapsed))

    def _annotate_frame(
        self,
        frame: VideoFrame,
        state: TargetLockVisionState,
    ) -> VideoFrame:
        if not state.active:
            return frame
        import cv2
        import numpy as np

        canvas = self._frame_array(frame, copy=True)
        channels = 1 if canvas.ndim == 2 else canvas.shape[2]
        if channels == 1:
            canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
            pixel_format = "bgr888"
            channels = 3
        else:
            pixel_format = frame.pixel_format
        if pixel_format == "bgr888":
            yellow = (0, 255, 255)
            green = (0, 255, 0)
            red = (0, 0, 255)
            white = (255, 255, 255)
            black = (0, 0, 0)
        else:
            yellow = (255, 255, 0, 255) if channels == 4 else (255, 255, 0)
            green = (0, 255, 0, 255) if channels == 4 else (0, 255, 0)
            red = (255, 0, 0, 255) if channels == 4 else (255, 0, 0)
            white = (255, 255, 255, 255) if channels == 4 else (255, 255, 255)
            black = (0, 0, 0, 255) if channels == 4 else (0, 0, 0)
        thickness = max(1, min(3, round(min(frame.width, frame.height) / 360)))
        zone = (
            round(frame.width * 0.25),
            round(frame.height * 0.10),
            round(frame.width * 0.75),
            round(frame.height * 0.90),
        )
        zone_color = (
            green
            if state.feedback == "success"
            else red
            if state.feedback == "failure"
            else yellow
        )
        cv2.rectangle(
            canvas,
            zone[:2],
            zone[2:],
            zone_color,
            thickness,
            cv2.LINE_8,
        )
        if state.target is not None:
            target = state.target
            cv2.rectangle(
                canvas,
                (target.x, target.y),
                (target.x + target.width, target.y + target.height),
                red,
                thickness,
                cv2.LINE_8,
            )
        lock_text = (
            "KILIT BASARILI"
            if state.feedback == "success"
            else "KILIT BASARISIZ"
            if state.feedback == "failure"
            else "KILITLENDI"
            if state.locked
            else (
                f"KILIT {state.lock_elapsed_s:.1f}/{state.lock_required_s:.1f} s"
                f"  PENCERE {state.window_elapsed_s:.1f}/"
                f"{state.window_duration_s:.1f} s"
            )
        )
        self._draw_outlined_text(
            canvas,
            lock_text,
            (zone[0] + 8, max(24, zone[1] + 24)),
            zone_color,
            black,
        )
        server_text = self._server_now().strftime("%H:%M:%S.%f")[:-3]
        (text_width, _text_height), _baseline = cv2.getTextSize(
            server_text,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            1,
        )
        self._draw_outlined_text(
            canvas,
            server_text,
            (max(8, frame.width - text_width - 10), 26),
            white,
            black,
        )
        return VideoFrame(
            width=frame.width,
            height=frame.height,
            data=canvas.tobytes(),
            pixel_format=pixel_format,
            stride=frame.width * channels,
            timestamp=frame.timestamp,
        )

    @staticmethod
    def _draw_outlined_text(
        canvas: Any,
        text: str,
        origin: tuple[int, int],
        color: Any,
        outline: Any,
    ) -> None:
        import cv2

        cv2.putText(
            canvas,
            text,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            outline,
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            text,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            color,
            1,
            cv2.LINE_AA,
        )

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

    @classmethod
    def _bgr_frame(cls, frame: VideoFrame) -> Any:
        import cv2

        image = cls._frame_array(frame, copy=False)
        if frame.pixel_format == "rgba8888":
            return cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
        if frame.pixel_format == "rgb888":
            return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        if frame.pixel_format == "gray8":
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image.copy()

    def _log_error(self, action: str, error: Exception) -> None:
        signature = f"{error.__class__.__name__}:{error}"
        if signature == self._last_error_signature:
            return
        self._last_error_signature = signature
        self._event(
            action,
            f"Target Lock görüntü işleme hatası: {error}",
            severity="danger",
            details={"model": str(self._model_path)},
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
