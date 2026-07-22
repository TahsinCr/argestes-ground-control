from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.models.video import VideoFrame
from src.services.logging_service import LoggingService
from src.services.processing.video import WeChatQrVideoService
from src.services.video import AutoReconnectVideoService, MutableVideoService


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "models" / "wechat_qrcode"


class VideoQrTests(unittest.TestCase):
    @staticmethod
    def _frame(width: int = 320, height: int = 240) -> VideoFrame:
        image = np.full((height, width, 4), 24, dtype=np.uint8)
        image[:, :, 3] = 255
        return VideoFrame(width, height, image.tobytes(), "rgba8888", width * 4)

    @staticmethod
    def _moving_qr_frame(x: int, y: int) -> tuple[VideoFrame, tuple]:
        qr = cv2.QRCodeEncoder_create().encode("MOVING-KIRLANGIC-QR")
        qr = cv2.resize(qr, (120, 120), interpolation=cv2.INTER_NEAREST)
        canvas = np.full((260, 380, 4), 28, dtype=np.uint8)
        canvas[:, :, 3] = 255
        canvas[y : y + 120, x : x + 120, :3] = cv2.cvtColor(
            qr,
            cv2.COLOR_GRAY2RGB,
        )
        points = (
            (float(x), float(y)),
            (float(x + 119), float(y)),
            (float(x + 119), float(y + 119)),
            (float(x), float(y + 119)),
        )
        return (
            VideoFrame(380, 260, canvas.tobytes(), "rgba8888", 380 * 4),
            points,
        )

    def test_wechat_models_decode_a_generated_qr(self) -> None:
        source = MutableVideoService()
        service = WeChatQrVideoService(source, MODEL_DIR)
        detector = service._create_detector()
        qr = cv2.QRCodeEncoder_create().encode("KIRLANGIC-WECHAT-TEST")
        qr = cv2.resize(qr, (240, 240), interpolation=cv2.INTER_NEAREST)
        image = cv2.cvtColor(
            cv2.copyMakeBorder(qr, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255),
            cv2.COLOR_GRAY2RGBA,
        )
        frame = VideoFrame(300, 300, image.tobytes(), "rgba8888", 1200)

        observations = service._detect(detector, frame)

        self.assertEqual(observations[0][0], "KIRLANGIC-WECHAT-TEST")
        self.assertEqual(len(observations[0][1]), 4)

    def test_wechat_edge_roi_error_falls_back_and_keeps_decoded_text(self) -> None:
        class FailingWechatDetector:
            def detectAndDecode(self, _image):
                raise cv2.error("forced edge ROI failure")

        logs = LoggingService()
        service = WeChatQrVideoService(
            MutableVideoService(),
            MODEL_DIR,
            logs.child("QR"),
        )
        qr = cv2.QRCodeEncoder_create().encode("EDGE-QR-CONTENT")
        qr = cv2.resize(qr, (240, 240), interpolation=cv2.INTER_NEAREST)
        image = cv2.cvtColor(
            cv2.copyMakeBorder(qr, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255),
            cv2.COLOR_GRAY2RGBA,
        )
        frame = VideoFrame(300, 300, image.tobytes(), "rgba8888", 1200)

        observations = service._detect(FailingWechatDetector(), frame)
        service._update_tracks(observations)
        annotated = service._annotate_frame(
            frame,
            service.qr_snapshot.detections,
        )

        self.assertEqual(observations[0][0], "EDGE-QR-CONTENT")
        self.assertEqual(service.qr_snapshot.decoded[0].data, "EDGE-QR-CONTENT")
        self.assertNotEqual(annotated.data, frame.data)
        self.assertIn("qr.wechat_fallback", [entry.action for entry in logs.history])
        self.assertNotIn("qr.processing_failed", [entry.action for entry in logs.history])

    def test_only_decoded_qr_text_is_drawn_and_same_qr_is_remembered(self) -> None:
        logs = LoggingService()
        service = WeChatQrVideoService(
            MutableVideoService(),
            MODEL_DIR,
            logs.child("QR"),
            max_missed_scans=2,
        )
        points = ((60.0, 50.0), (250.0, 50.0), (250.0, 190.0), (60.0, 190.0))

        service._update_tracks([("", points)])
        detected = service.qr_snapshot.detections[0]
        self.assertFalse(detected.decoded)
        plain_frame = self._frame()
        detected_frame = service._annotate_frame(plain_frame, (detected,))
        self.assertEqual(detected_frame.data, plain_frame.data)

        service._update_tracks([("QR-DATA-27", points)])
        decoded = service.qr_snapshot.detections[0]
        self.assertTrue(decoded.decoded)
        self.assertFalse(decoded.submitted)
        decoded_frame = service._annotate_frame(self._frame(), (decoded,))
        decoded_pixels = np.frombuffer(decoded_frame.data, dtype=np.uint8).reshape(240, 320, 4)
        self.assertTrue(np.any(np.all(decoded_pixels == (96, 214, 68, 255), axis=2)))
        self.assertFalse(np.any(np.all(decoded_pixels == (235, 72, 72, 255), axis=2)))
        self.assertTrue(np.all(decoded_pixels[50, 60] == (24, 24, 24, 255)))

        service.mark_qr_submitted("QR-DATA-27")
        submitted = service.qr_snapshot.detections[0]
        self.assertTrue(submitted.submitted)
        green_frame = service._annotate_frame(self._frame(), (submitted,))
        green_pixels = np.frombuffer(green_frame.data, dtype=np.uint8).reshape(240, 320, 4)
        self.assertTrue(np.any(np.all(green_pixels == (96, 214, 68, 255), axis=2)))

        service._update_tracks([("", points)])
        self.assertTrue(service.qr_snapshot.detections[0].decoded)
        service._update_tracks([])
        service._update_tracks([])
        self.assertEqual(len(service.qr_snapshot.detections), 1)
        service._update_tracks([])
        self.assertEqual(service.qr_snapshot.detections, ())
        empty_frame = self._frame()
        self.assertEqual(
            service._annotate_frame(empty_frame, ()).data,
            empty_frame.data,
        )

        service._update_tracks([("QR-DATA-27", points)])
        self.assertTrue(service.qr_snapshot.detections[0].submitted)
        self.assertEqual(
            [entry.action for entry in logs.history],
            ["qr.detected", "qr.decoded", "qr.lost"],
        )

    def test_only_first_qr_is_rendered_and_detector_loss_removes_text(self) -> None:
        service = WeChatQrVideoService(
            MutableVideoService(),
            MODEL_DIR,
            max_missed_scans=1,
        )
        first_points = (
            (30.0, 40.0),
            (180.0, 40.0),
            (180.0, 190.0),
            (30.0, 190.0),
        )
        second_points = (
            (220.0, 60.0),
            (300.0, 60.0),
            (300.0, 140.0),
            (220.0, 140.0),
        )

        service._update_tracks(
            [
                ("FIRST-QR", first_points),
                ("SECOND-QR", second_points),
            ]
        )

        self.assertEqual(len(service.qr_snapshot.detections), 1)
        self.assertEqual(service.qr_snapshot.decoded[0].data, "FIRST-QR")

        # Optik akis son konumu tasimis olsa bile art arda bos detector
        # sonuclari QR metnini ekrandan kaldirmalidir.
        first_frame, _points = self._moving_qr_frame(70, 55)
        moved_frame, _points = self._moving_qr_frame(82, 61)
        service._track_qr_on_frame(first_frame)
        service._track_qr_on_frame(moved_frame)
        service._update_tracks([])
        service._update_tracks([])

        self.assertEqual(service.qr_snapshot.detections, ())
        plain = self._frame()
        self.assertEqual(service._annotate_frame(plain, ()).data, plain.data)

        service._update_tracks([("SECOND-QR", second_points)])
        self.assertEqual(service.qr_snapshot.detections, ())

    def test_submission_keeps_green_text_without_adding_a_qr_box(self) -> None:
        service = WeChatQrVideoService(MutableVideoService(), MODEL_DIR)
        points = ((60.0, 50.0), (250.0, 50.0), (250.0, 190.0), (60.0, 190.0))
        service._update_tracks([("FAST-ACK-QR", points)])

        service.mark_qr_submitted("FAST-ACK-QR")
        pending = service.qr_snapshot.detections[0]
        self.assertFalse(pending.submitted)

        first_frame = service._annotate_frame(self._frame(), (pending,))
        first_pixels = np.frombuffer(first_frame.data, dtype=np.uint8).reshape(
            240, 320, 4
        )
        self.assertTrue(
            np.any(np.all(first_pixels == (96, 214, 68, 255), axis=2))
        )
        self.assertFalse(
            np.any(np.all(first_pixels == (235, 72, 72, 255), axis=2))
        )

        accepted = service.qr_snapshot.detections[0]
        self.assertTrue(accepted.submitted)
        next_frame = service._annotate_frame(self._frame(), (accepted,))
        next_pixels = np.frombuffer(next_frame.data, dtype=np.uint8).reshape(
            240, 320, 4
        )
        self.assertTrue(
            np.any(np.all(next_pixels == (96, 214, 68, 255), axis=2))
        )

    def test_qr_polygon_follows_motion_between_wechat_scans(self) -> None:
        service = WeChatQrVideoService(MutableVideoService(), MODEL_DIR)
        first_frame, first_points = self._moving_qr_frame(70, 55)
        moved_frame, _moved_points = self._moving_qr_frame(94, 67)
        service._update_tracks([("MOVING-KIRLANGIC-QR", first_points)])

        service._track_qr_on_frame(first_frame)
        service._track_qr_on_frame(moved_frame)

        tracked = service.qr_snapshot.detections[0]
        original_center = np.mean(np.asarray(first_points), axis=0)
        tracked_center = np.mean(np.asarray(tracked.points), axis=0)
        movement = tracked_center - original_center
        self.assertAlmostEqual(float(movement[0]), 24.0, delta=3.0)
        self.assertAlmostEqual(float(movement[1]), 12.0, delta=3.0)

    def test_processing_worker_publishes_decoded_state_without_blocking_source(self) -> None:
        class Detector:
            def detectAndDecode(self, _image):
                return (
                    ("ASYNC-QR",),
                    (
                        np.array(
                            [[40.0, 40.0], [200.0, 40.0], [200.0, 180.0], [40.0, 180.0]],
                            dtype=np.float32,
                        ),
                    ),
                )

        source = MutableVideoService()
        service = WeChatQrVideoService(
            source,
            MODEL_DIR,
            scan_interval_s=0.001,
            detector=Detector(),
        )
        decoded = threading.Event()
        frames: list[VideoFrame] = []
        service.frames.subscribe(frames.append)
        service.qr_changed.subscribe(
            lambda snapshot: decoded.set() if snapshot.decoded else None
        )
        service.set_qr_active(True)
        service.start()
        service.push(self._frame())
        self.assertTrue(decoded.wait(1.0))
        service.push(self._frame())
        decoded_data = service.qr_snapshot.decoded[0].data
        service.stop()

        self.assertGreaterEqual(len(frames), 2)
        self.assertEqual(decoded_data, "ASYNC-QR")
        self.assertEqual(service.qr_snapshot.detections, ())

    def test_qr_detector_stays_idle_until_kamikaze_activation(self) -> None:
        class Detector:
            def __init__(self) -> None:
                self.calls = 0

            def detectAndDecode(self, _image):
                self.calls += 1
                return (), ()

        detector = Detector()
        service = WeChatQrVideoService(
            MutableVideoService(),
            MODEL_DIR,
            scan_interval_s=0.001,
            detector=detector,
        )
        service.start()
        service.push(self._frame())
        time.sleep(0.05)
        self.assertEqual(detector.calls, 0)
        self.assertFalse(service.qr_active)

        service.set_qr_active(True)
        service.push(self._frame())
        deadline = time.monotonic() + 1.0
        while detector.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        service.stop()

        self.assertGreaterEqual(detector.calls, 1)

    def test_video_source_retries_until_camera_recovers(self) -> None:
        class FlakyVideo(MutableVideoService):
            def __init__(self) -> None:
                super().__init__()
                self.attempts = 0

            def reconnect(self) -> None:
                self.attempts += 1
                if self.attempts < 2:
                    raise ConnectionError("camera offline")
                super().reconnect()

        source = FlakyVideo()
        service = AutoReconnectVideoService(source, retry_interval_s=0.02)
        service.start()
        deadline = time.monotonic() + 1.0
        while not service.connected and time.monotonic() < deadline:
            time.sleep(0.01)
        service.stop()

        self.assertGreaterEqual(source.attempts, 2)
        self.assertTrue(source.connected is False)


if __name__ == "__main__":
    unittest.main()
