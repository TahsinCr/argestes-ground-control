"""Measure the application's GStreamer USB camera capture path without saving frames.

Example: .venv/bin/python tools/camera_capture_probe.py --device /dev/video0
The camera may be covered; black frames are still valid capture samples.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extensions.camera.capture import GStreamerVideoService


class MeasuredCapture(GStreamerVideoService):
    def __init__(self, *args, sample_limit: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.callback_seconds = deque(maxlen=sample_limit)
        self._samples_lock = threading.Lock()

    def _on_sample(self, sink):
        started = time.perf_counter()
        try:
            return super()._on_sample(sink)
        finally:
            with self._samples_lock:
                self.callback_seconds.append(time.perf_counter() - started)


def _percentiles(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}

    def percentile(fraction):
        return (
            ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))] * 1000
        )

    return {
        "count": len(ordered),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": ordered[-1] * 1000,
    }


def _peak_rss_mib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    return value / (1024 if sys.platform.startswith("linux") else 1024 * 1024)


def _current_rss_mib():
    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, IndexError, ValueError):
        return _peak_rss_mib()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--startup-timeout", type=float, default=6)
    parser.add_argument("--samples", type=int, default=100_000)
    args = parser.parse_args()
    if args.seconds <= 0 or args.startup_timeout <= 0 or args.samples <= 0:
        parser.error("duration, startup timeout and sample limit must be positive")
    if not Path(args.device).exists():
        parser.error(f"camera device not found: {args.device}")

    frame_arrivals = deque(maxlen=args.samples)
    frame_bytes = [0]
    frames_delivered = [0]

    def on_frame(frame):
        frame_arrivals.append(time.perf_counter())
        frame_bytes[0] += len(frame.data)
        frames_delivered[0] += 1

    service = MeasuredCapture("usb", args.device, sample_limit=args.samples)
    subscription = service.frames.subscribe(on_frame)
    rss_before = _current_rss_mib()
    rss_peak = [rss_before]
    rss_stop = threading.Event()

    def sample_rss():
        while not rss_stop.wait(0.1):
            rss_peak[0] = max(rss_peak[0], _current_rss_mib())

    rss_worker = threading.Thread(
        target=sample_rss, name="camera-probe-rss", daemon=True
    )
    rss_worker.start()
    wall_started = time.perf_counter()
    cpu_started = None
    sample_started = None
    sample_deadline = None
    try:
        service.start()
        startup_deadline = wall_started + args.startup_timeout
        while sample_started is None and time.perf_counter() < startup_deadline:
            if frame_arrivals:
                sample_started = time.perf_counter()
                sample_deadline = sample_started + args.seconds
                cpu_started = time.process_time()
                break
            time.sleep(0.02)
        if sample_started is None:
            raise RuntimeError(
                f"No frames received from {args.device} within {args.startup_timeout:g}s"
            )
        while time.perf_counter() < sample_deadline:
            time.sleep(min(0.05, max(0.0, sample_deadline - time.perf_counter())))
        sample_finished = time.perf_counter()
        cpu_finished = time.process_time()
        stats = service.stats
        service.stop()
        with service._samples_lock:
            callback_samples = tuple(service.callback_seconds)
        arrivals = tuple(frame_arrivals)
        elapsed = sample_finished - sample_started
        intervals = [right - left for left, right in zip(arrivals, arrivals[1:])]
        report = {
            "device": args.device,
            "duration_s": elapsed,
            "frames_delivered": frames_delivered[0],
            "reported_fps": stats.fps,
            "resolution": [stats.width, stats.height],
            "frame_size_bytes": (stats.width * stats.height * 4),
            "copied_frame_data_mib": frame_bytes[0] / (1024 * 1024),
            "copied_data_mib_s": frame_bytes[0] / (1024 * 1024) / elapsed,
            "capture_callback": _percentiles(callback_samples),
            "frame_interval": _percentiles(intervals),
            "process_cpu_percent": (cpu_finished - cpu_started)
            / max(elapsed, 1e-9)
            * 100,
            "rss_before_capture_mib": rss_before,
            "rss_peak_during_capture_mib": rss_peak[0],
            "rss_growth_mib": max(0.0, rss_peak[0] - rss_before),
            "peak_rss_mib": _peak_rss_mib(),
            "black_frames_are_not_filtered": True,
            "frames_saved": False,
        }
        print(json.dumps(report, indent=2))
        return 0
    finally:
        rss_stop.set()
        rss_worker.join(timeout=0.5)
        subscription.cancel()
        service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
