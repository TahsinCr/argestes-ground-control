from __future__ import annotations

import hashlib
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from target_tracker.lock_logic.lock_timer import LockTimer
from target_tracker.lock_logic.vurus_alani import VurusAlani
from target_tracker.perception.yolo_infer import YoloInfer
from target_tracker.tracking.bytetrack_wrapper import ByteTrackWrapper

from src.core.events import EventStream
from src.models.commands import CommandRequest, CommandResult
from src.models.target_lock import TargetBoundingBox, TargetLockAcquisition
from src.models.video import VideoFrame
from src.services.orchestration.target_lock import TargetLockWorkflow
from src.services.processing.target_lock import TargetLockVideoService
from src.services.processing.telemetry import MutableTelemetryService
from src.services.video import MutableVideoService


ROOT = Path(__file__).resolve().parents[1]


class _Detector:
    ready = True
    backend = "test"
    last_error = ""

    def infer(self, _frame):
        return []


class _Commands:
    def __init__(self) -> None:
        self.dispatched = EventStream()
        self.completed = EventStream()


class _Vision:
    def __init__(self) -> None:
        self.target_acquired = EventStream()
        self.active = False

    def set_target_lock_active(self, active: bool) -> None:
        self.active = bool(active)


class _Competition:
    def __init__(self) -> None:
        self.events = []

    def submit_lock_event(self, event) -> None:
        self.events.append(event)


class TargetLockTests(unittest.TestCase):
    @staticmethod
    def _frame(width: int = 640, height: int = 480) -> VideoFrame:
        image = np.full((height, width, 4), 24, dtype=np.uint8)
        image[:, :, 3] = 255
        return VideoFrame(width, height, image.tobytes(), "rgba8888", width * 4)

    def test_official_model_checksum_and_onnx_derivative_exist(self) -> None:
        pt_path = ROOT / "data" / "models" / "target_lock" / "yolo26s.pt"
        onnx_path = pt_path.with_suffix(".onnx")
        self.assertEqual(
            hashlib.sha256(pt_path.read_bytes()).hexdigest(),
            "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b",
        )
        self.assertGreater(onnx_path.stat().st_size, 30_000_000)

    def test_pt_path_uses_onnxruntime_fallback_in_yki_environment(self) -> None:
        detector = YoloInfer(
            str(ROOT / "data" / "models" / "target_lock" / "yolo26s.pt"),
            conf_threshold=0.99,
        )

        detections = detector.infer(np.zeros((640, 640, 3), dtype=np.uint8))

        self.assertTrue(detector.ready, detector.last_error)
        self.assertEqual(detector.backend, "onnxruntime")
        self.assertEqual(detections, [])

    def test_strike_zone_matches_teknofest_2026_figure(self) -> None:
        zone = VurusAlani(1000, 500)
        self.assertEqual(zone.bounds, (250, 50, 750, 450))
        self.assertTrue(zone.is_inside(500, 250, 61, 10))
        self.assertFalse(zone.is_inside(500, 250, 49, 24))
        self.assertFalse(zone.is_inside(200, 250, 100, 100))

    def test_lock_timer_accumulates_four_valid_seconds_inside_five_second_window(self) -> None:
        timer = LockTimer()
        for _index in range(10):
            timer.update(0.1, True)
        for _index in range(5):
            timer.update(0.1, False)
        for _index in range(30):
            timer.update(0.1, True)

        self.assertTrue(timer.locked)
        self.assertAlmostEqual(timer.invalid_elapsed, 0.5, places=6)

        timer.reset()
        for _index in range(20):
            timer.update(0.1, True)
        failed = False
        for _index in range(30):
            timer.update(0.1, False)
            failed = failed or timer.just_failed

        self.assertTrue(failed)
        self.assertEqual(timer.current_progress, 0.0)
        self.assertFalse(timer.locked)

    def test_lock_timer_uses_real_slow_inference_intervals(self) -> None:
        timer = LockTimer()
        timer.update(0.0, True)
        for _index in range(5):
            timer.update(0.8, True)

        self.assertTrue(timer.locked)
        self.assertAlmostEqual(timer.elapsed, 4.0)
        self.assertAlmostEqual(timer.window_elapsed, 4.0)

    def test_confirmed_track_reacquires_without_four_frame_warmup(self) -> None:
        tracker = ByteTrackWrapper(
            track_high_thresh=0.35,
            track_low_thresh=0.10,
            match_thresh=0.30,
            new_track_thresh=0.35,
            max_lost_frames=10,
            min_hit_streak=2,
        )
        detection = [270.0, 200.0, 370.0, 280.0, 0.92, 4]

        self.assertEqual(tracker.update([detection]), [])
        self.assertEqual(len(tracker.update([detection])), 1)
        self.assertEqual(tracker.update([]), [])
        self.assertEqual(len(tracker.update([detection])), 1)

    def test_target_service_draws_yellow_zone_red_target_and_acquires_once(self) -> None:
        telemetry = MutableTelemetryService()
        service = TargetLockVideoService(
            MutableVideoService(),
            ROOT / "data" / "models" / "target_lock" / "yolo26s.pt",
            telemetry,
            detector=_Detector(),
            lock_duration_s=0.1,
        )
        acquisitions = []
        service.target_acquired.subscribe(acquisitions.append)
        service.set_target_lock_active(True)
        frame = self._frame()
        detection = [270.0, 200.0, 370.0, 280.0, 0.92, 4]

        now = 1.0
        for _index in range(9):
            service._consume_detections(frame, [detection], now)
            now += 0.04

        state = service.target_lock_state
        self.assertTrue(state.locked)
        self.assertEqual(state.feedback, "success")
        self.assertAlmostEqual(state.lock_required_s, 0.1)
        self.assertAlmostEqual(state.window_duration_s, 1.1)
        self.assertEqual(len(acquisitions), 1)
        self.assertTrue(telemetry.current.target_track.locked)
        annotated = service._annotate_frame(frame, state)
        pixels = np.frombuffer(annotated.data, dtype=np.uint8).reshape(480, 640, 4)
        self.assertTrue(np.any(np.all(pixels == (0, 255, 0, 255), axis=2)))
        self.assertTrue(np.any(np.all(pixels == (255, 0, 0, 255), axis=2)))

        service._expire_feedback(time.monotonic() + 2.0)
        restored = service._annotate_frame(frame, service.target_lock_state)
        restored_pixels = np.frombuffer(restored.data, dtype=np.uint8).reshape(
            480, 640, 4
        )
        self.assertTrue(
            np.any(np.all(restored_pixels == (255, 255, 0, 255), axis=2))
        )

        for _index in range(3):
            service._consume_detections(frame, [detection], now)
            now += 0.04
        self.assertEqual(len(acquisitions), 1)

    def test_failed_lock_attempt_flashes_red_then_returns_to_yellow(self) -> None:
        service = TargetLockVideoService(
            MutableVideoService(),
            ROOT / "data" / "models" / "target_lock" / "yolo26s.pt",
            MutableTelemetryService(),
            detector=_Detector(),
            lock_duration_s=4.0,
        )
        service.set_target_lock_active(True)
        frame = self._frame()
        for _index in range(7):
            service._lock_timer.update(0.5, True)
        for _index in range(2):
            service._lock_timer.update(0.5, False)
        service._last_inference_at = 1.0

        service._consume_detections(frame, [], 1.5)

        failed = service.target_lock_state
        self.assertEqual(failed.feedback, "failure")
        self.assertAlmostEqual(service._lock_timer.last_attempt_elapsed, 3.5)
        self.assertAlmostEqual(service._lock_timer.last_attempt_window_elapsed, 5.0)
        red_frame = service._annotate_frame(frame, failed)
        red_pixels = np.frombuffer(red_frame.data, dtype=np.uint8).reshape(
            480, 640, 4
        )
        self.assertTrue(np.any(np.all(red_pixels == (255, 0, 0, 255), axis=2)))

        service._expire_feedback(2.51)
        restored = service._annotate_frame(frame, service.target_lock_state)
        restored_pixels = np.frombuffer(restored.data, dtype=np.uint8).reshape(
            480, 640, 4
        )
        self.assertTrue(
            np.any(np.all(restored_pixels == (255, 255, 0, 255), axis=2))
        )

    def test_slow_result_is_discarded_after_target_lock_is_stopped(self) -> None:
        class SlowDetector(_Detector):
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()

            def infer(self, _frame):
                self.entered.set()
                self.release.wait(1.0)
                return [[270.0, 200.0, 370.0, 280.0, 0.92, 4]]

        source = MutableVideoService()
        telemetry = MutableTelemetryService()
        detector = SlowDetector()
        service = TargetLockVideoService(
            source,
            ROOT / "data" / "models" / "target_lock" / "yolo26s.pt",
            telemetry,
            detector=detector,
            inference_interval_s=0.001,
        )
        service.start()
        service.set_target_lock_active(True)
        service.push(self._frame())
        self.assertTrue(detector.entered.wait(1.0))

        service.set_target_lock_active(False)
        detector.release.set()
        time.sleep(0.05)

        self.assertFalse(service.target_lock_state.active)
        self.assertEqual(telemetry.current.target_track.width, 0)
        service.stop()

    def test_target_lock_command_activates_and_submits_server_event_once(self) -> None:
        commands = _Commands()
        vision = _Vision()
        competition = _Competition()
        workflow = TargetLockWorkflow(
            commands,  # type: ignore[arg-type]
            vision,  # type: ignore[arg-type]
            competition,  # type: ignore[arg-type]
        )
        workflow.start()
        request = CommandRequest("SET_MISSION_TASK", {"task": "TARGET_LOCK"}, "test")
        commands.completed.publish(
            CommandResult(request, True, "ok", {"task": "TARGET_LOCK"})
        )
        self.assertTrue(vision.active)

        acquisition = TargetLockAcquisition(
            ended_at=datetime.now(UTC),
            target=TargetBoundingBox(270, 200, 100, 80, 0.9, 7),
        )
        vision.target_acquired.publish(acquisition)
        deadline = time.monotonic() + 1.0
        while not workflow.state.submitted and time.monotonic() < deadline:
            time.sleep(0.01)
        vision.target_acquired.publish(acquisition)
        time.sleep(0.02)

        self.assertTrue(workflow.state.submitted)
        self.assertEqual(len(competition.events), 1)
        self.assertTrue(competition.events[0].autonomous)
        workflow.stop()


if __name__ == "__main__":
    unittest.main()
