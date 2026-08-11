from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from src.core.events import EventBus
from src.models.video import VideoFrame, VideoStreamStats
from src.services.contracts.video import VideoService
from src.services.logging_service import ServiceLogger


class MutableVideoService(VideoService):
    def __init__(self, logger: ServiceLogger | None = None) -> None:
        self._frames = EventBus[VideoFrame]()
        self._stats_changed = EventBus[VideoStreamStats]()
        self._connection_changed = EventBus[bool]()
        self._logger = logger
        self._connected = False
        self._connection_lock = threading.RLock()
        self._stats = VideoStreamStats()
        self._stats_lock = threading.RLock()

    @property
    def connected(self) -> bool:
        with self._connection_lock:
            return self._connected

    @property
    def connection_changed(self) -> EventBus[bool]:
        return self._connection_changed

    @property
    def frames(self) -> EventBus[VideoFrame]:
        return self._frames

    @property
    def stats(self) -> VideoStreamStats:
        with self._stats_lock:
            return self._stats

    @property
    def stats_changed(self) -> EventBus[VideoStreamStats]:
        return self._stats_changed

    def start(self) -> None:
        self._set_connected(True)

    def stop(self) -> None:
        self._set_connected(False)

    def configure(
        self,
        *,
        source_type: str,
        source_uri: str,
        latency_ms: int,
        codec: str,
        frame_timeout_s: float,
    ) -> None:
        del source_type, source_uri, latency_ms, codec, frame_timeout_s

    def reconnect(self) -> None:
        self.stop()
        self.start()

    def push(self, frame: VideoFrame) -> None:
        self._set_connected(True)
        self._frames.publish(frame)
        previous = self.stats
        self._publish_stats(
            VideoStreamStats(
                connected=True,
                quality_percent=100,
                frames_received=previous.frames_received + 1,
                width=frame.width,
                height=frame.height,
                frame_age_s=0.0,
            )
        )

    def _set_connected(self, connected: bool) -> None:
        value = bool(connected)
        with self._connection_lock:
            if value == self._connected:
                return
            self._connected = value
        self._connection_changed.publish(value)
        current = self.stats
        self._publish_stats(
            VideoStreamStats(
                connected=value,
                quality_percent=current.quality_percent if value else 0,
                frames_received=current.frames_received,
                fps=current.fps if value else 0.0,
                bitrate_kbps=current.bitrate_kbps if value else 0.0,
                width=current.width,
                height=current.height,
                frame_age_s=current.frame_age_s,
            )
        )

    def _publish_stats(self, stats: VideoStreamStats) -> None:
        with self._stats_lock:
            if stats == self._stats:
                return
            self._stats = stats
        self._stats_changed.publish(stats)


class GStreamerVideoService(MutableVideoService):
    """Convert GStreamer appsink frames into the shared VideoFrame model."""

    def __init__(
        self,
        source_type: str,
        source_uri: str,
        logger: ServiceLogger | None = None,
        *,
        latency_ms: int = 100,
        codec: str = "h265",
        frame_timeout_s: float = 3.0,
    ) -> None:
        super().__init__(logger)
        self._source_type = source_type.strip().lower()
        self._source_uri = source_uri.strip()
        self._latency_ms = max(0, int(latency_ms))
        self._codec = codec.strip().lower()
        self._frame_timeout_s = float(frame_timeout_s)
        if self._codec not in {"h264", "h265", "mjpeg"}:
            raise ValueError(f"Desteklenmeyen GStreamer codec: {codec}")
        if self._frame_timeout_s <= 0:
            raise ValueError("Video kare zaman aşımı pozitif olmalı")
        self._pipeline: Any | None = None
        self._gst: Any | None = None
        self._bus_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._frame_count = 0
        self._last_frame_at = 0.0
        self._last_stats_at = 0.0
        self._peak_fps = 0.0
        self._frame_times: deque[float] = deque()
        self._transport_samples: deque[tuple[float, int]] = deque()
        self._measurement_lock = threading.RLock()
        self._started = False

    @property
    def pipeline_description(self) -> str:
        return self.build_pipeline(
            self._source_type,
            self._source_uri,
            self._latency_ms,
            self._codec,
        )

    def configure(
        self,
        *,
        source_type: str,
        source_uri: str,
        latency_ms: int,
        codec: str,
        frame_timeout_s: float,
    ) -> None:
        normalized_type = source_type.strip().lower()
        normalized_uri = source_uri.strip()
        normalized_codec = codec.strip().lower()
        if normalized_codec not in {"h264", "h265", "mjpeg"}:
            raise ValueError(f"Desteklenmeyen GStreamer codec: {codec}")
        if normalized_type not in {"http", "rtsp", "udp", "usb", "file", "pipeline"}:
            raise ValueError(f"Desteklenmeyen GStreamer kaynak tipi: {source_type}")
        if not normalized_uri:
            raise ValueError("Video kaynağı adresi boş olamaz")
        if not 0 <= int(latency_ms) <= 10_000:
            raise ValueError("Video gecikmesi 0..10000 ms aralığında olmalı")
        if float(frame_timeout_s) <= 0:
            raise ValueError("Video kare zaman aşımı pozitif olmalı")
        self.stop()
        self._source_type = normalized_type
        self._source_uri = normalized_uri
        self._latency_ms = int(latency_ms)
        self._codec = normalized_codec
        self._frame_timeout_s = float(frame_timeout_s)

    def reconnect(self) -> None:
        self.stop()
        self.start()

    def start(self) -> None:
        if self._started:
            return
        if not self._source_uri:
            self._set_connected(False)
            return
        Gst = self._load_gstreamer()
        description = self.pipeline_description
        pipeline = Gst.parse_launch(description)
        sink = pipeline.get_by_name("yki_sink")
        if sink is None:
            raise ValueError("GStreamer pipeline yki_sink appsink öğesi üretmedi")
        sink.connect("new-sample", self._on_sample)
        transport_meter = pipeline.get_by_name("yki_transport_stats")
        if transport_meter is not None:
            transport_pad = transport_meter.get_static_pad("src")
            if transport_pad is not None:
                transport_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_transport_buffer)
        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer pipeline PLAYING durumuna geçemedi")
        self._pipeline = pipeline
        self._stop_event.clear()
        self._last_frame_at = 0.0
        self._frame_count = 0
        self._last_stats_at = 0.0
        self._peak_fps = 0.0
        with self._measurement_lock:
            self._frame_times.clear()
            self._transport_samples.clear()
        self._publish_stats(VideoStreamStats())
        self._started = True
        self._bus_thread = threading.Thread(
            target=self._watch_bus,
            name="GStreamerBus",
            daemon=True,
        )
        self._bus_thread.start()
        self._event(
            "video.pipeline_started",
            f"GStreamer {self._source_type} video pipeline başlatıldı",
            details={
                "source_type": self._source_type,
                "codec": self._codec,
                "latency_ms": self._latency_ms,
                "frame_timeout_s": self._frame_timeout_s,
            },
        )

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        pipeline = self._pipeline
        self._pipeline = None
        if pipeline is not None and self._gst is not None:
            pipeline.set_state(self._gst.State.NULL)
        thread = self._bus_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._bus_thread = None
        self._started = False
        self._set_connected(False)
        self._event(
            "video.pipeline_stopped",
            "GStreamer video pipeline durduruldu",
            details={"frame_count": self._frame_count},
        )

    def _on_sample(self, sink: Any) -> Any:
        Gst = self._gst
        if Gst is None:
            return 0
        try:
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.ERROR
            caps = sample.get_caps()
            structure = caps.get_structure(0)
            width = int(structure.get_value("width"))
            height = int(structure.get_value("height"))
            buffer = sample.get_buffer()
            success, info = buffer.map(Gst.MapFlags.READ)
            if not success:
                return Gst.FlowReturn.ERROR
            try:
                data = bytes(info.data)
            finally:
                buffer.unmap(info)
            was_connected = self.connected
            self._last_frame_at = time.monotonic()
            frame = VideoFrame(
                width=width,
                height=height,
                data=data,
                pixel_format="rgba8888",
                stride=width * 4,
            )
            self._set_connected(True)
            self._frames.publish(frame)
            self._frame_count += 1
            self._record_frame_stats(width, height, self._last_frame_at)
            if not was_connected and self._frame_count > 1:
                self._event(
                    "video.stream_recovered",
                    "Video kare akışı yeniden başladı",
                    severity="success",
                )
            if self._frame_count == 1:
                self._event(
                    "video.first_frame_received",
                    f"İlk video karesi alındı ({width}x{height})",
                    severity="success",
                    details={"width": width, "height": height},
                )
            return Gst.FlowReturn.OK
        except Exception as exc:
            self._event(
                "video.frame_failed",
                f"Video karesi işlenemedi: {exc}",
                severity="danger",
            )
            return Gst.FlowReturn.ERROR

    def _on_transport_buffer(self, _pad: Any, info: Any) -> Any:
        Gst = self._gst
        if Gst is None:
            return 0
        buffer = info.get_buffer()
        if buffer is not None:
            now = time.monotonic()
            with self._measurement_lock:
                self._transport_samples.append((now, int(buffer.get_size())))
        return Gst.PadProbeReturn.OK

    def _record_frame_stats(self, width: int, height: int, now: float) -> None:
        with self._measurement_lock:
            self._frame_times.append(now)
            cutoff = now - 2.0
            while self._frame_times and self._frame_times[0] < cutoff:
                self._frame_times.popleft()
            while self._transport_samples and self._transport_samples[0][0] < cutoff:
                self._transport_samples.popleft()
            frame_times = tuple(self._frame_times)
            transport_samples = tuple(self._transport_samples)

        if now - self._last_stats_at < 0.45 and self._frame_count > 1:
            return
        self._last_stats_at = now
        fps = 0.0
        if len(frame_times) >= 2:
            span = frame_times[-1] - frame_times[0]
            if span > 0.0:
                fps = (len(frame_times) - 1) / span
        self._peak_fps = max(self._peak_fps, fps)
        target_fps = max(self._peak_fps, 1.0)
        delivery_score = min(1.0, fps / target_fps) if fps > 0.0 else 1.0
        jitter_score = 1.0
        if len(frame_times) >= 4:
            intervals = [
                later - earlier
                for earlier, later in zip(frame_times, frame_times[1:])
            ]
            mean_interval = sum(intervals) / len(intervals)
            if mean_interval > 0.0:
                variance = sum(
                    (interval - mean_interval) ** 2 for interval in intervals
                ) / len(intervals)
                jitter_ratio = variance ** 0.5 / mean_interval
                jitter_score = max(0.0, 1.0 - min(1.0, jitter_ratio * 2.0))
        quality = int(round(100.0 * (delivery_score * 0.75 + jitter_score * 0.25)))

        bitrate_kbps = 0.0
        if transport_samples:
            transport_span = max(0.5, now - transport_samples[0][0])
            bitrate_kbps = sum(size for _timestamp, size in transport_samples) * 8.0
            bitrate_kbps /= transport_span * 1000.0
        self._publish_stats(
            VideoStreamStats(
                connected=True,
                quality_percent=max(1, min(100, quality)),
                frames_received=self._frame_count,
                fps=fps,
                bitrate_kbps=bitrate_kbps,
                width=width,
                height=height,
                frame_age_s=0.0,
            )
        )

    def _watch_bus(self) -> None:
        pipeline = self._pipeline
        Gst = self._gst
        if pipeline is None or Gst is None:
            return
        bus = pipeline.get_bus()
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED
        while not self._stop_event.is_set():
            message = bus.timed_pop_filtered(250 * Gst.MSECOND, mask)
            if message is None:
                self._check_frame_timeout()
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                self._event(
                    "video.pipeline_error",
                    f"GStreamer hatası: {error}",
                    severity="danger",
                    details={"debug": debug or ""},
                )
                self._set_connected(False)
                self._stop_event.set()
            elif message.type == Gst.MessageType.EOS:
                self._event("video.end_of_stream", "Video akışı sona erdi", severity="warning")
                self._set_connected(False)
                self._stop_event.set()
            self._check_frame_timeout()

    def _check_frame_timeout(self, now: float | None = None) -> None:
        if not self.connected or self._last_frame_at <= 0:
            return
        elapsed = (time.monotonic() if now is None else float(now)) - self._last_frame_at
        if elapsed <= self._frame_timeout_s:
            return
        self._set_connected(False)
        self._event(
            "video.frame_timeout",
            f"Video kare akışı {elapsed:.1f} saniyedir alınamıyor",
            severity="warning",
            details={"frame_timeout_s": self._frame_timeout_s},
        )

    def _load_gstreamer(self) -> Any:
        if self._gst is not None:
            return self._gst
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                "GStreamer Python bağları bulunamadı; PyGObject ve GStreamer kurulmalı"
            ) from exc
        Gst.init(None)
        user_plugin_path = (
            Path.home() / ".local" / "share" / "kirlangic-yki" / "gstreamer-1.0"
        )
        if user_plugin_path.is_dir():
            Gst.Registry.get().scan_path(str(user_plugin_path))
        self._gst = Gst
        return Gst

    @classmethod
    def build_pipeline(
        cls,
        source_type: str,
        source_uri: str,
        latency_ms: int = 100,
        codec: str = "h265",
    ) -> str:
        source_type = source_type.strip().lower()
        source_uri = source_uri.strip()
        codec = codec.strip().lower()
        if codec not in {"h264", "h265", "mjpeg"}:
            raise ValueError(f"Desteklenmeyen GStreamer codec: {codec}")
        if not source_uri:
            raise ValueError("Video kaynağı adresi boş olamaz")
        if source_type == "pipeline":
            source = source_uri
            if "appsink" in source and "name=yki_sink" in source:
                return source
        elif source_type == "http":
            parsed = urlparse(source_uri)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Geçersiz HTTP video adresi: {source_uri}")
            source = (
                f'souphttpsrc location="{cls._quote(source_uri)}" is-live=true '
                "do-timestamp=true timeout=5 ! multipartdemux "
                "! identity name=yki_transport_stats silent=true ! jpegdec"
            )
        elif source_type == "rtsp":
            source = (
                f'rtspsrc location="{cls._quote(source_uri)}" latency={max(0, int(latency_ms))} '
                "drop-on-latency=true ! decodebin"
            )
        elif source_type == "udp":
            if "!" in source_uri:
                source = source_uri
            else:
                port = cls._udp_port(source_uri)
                encoding = codec.upper()
                source = (
                    f'udpsrc port={port} caps="application/x-rtp,media=(string)video,'
                    f'encoding-name=(string){encoding},payload=(int)96" '
                    f"! rtp{codec}depay ! {codec}parse ! avdec_{codec}"
                )
        elif source_type == "usb":
            source = f'v4l2src device="{cls._quote(source_uri)}"'
        elif source_type == "file":
            source = f'filesrc location="{cls._quote(source_uri)}" ! decodebin'
        else:
            raise ValueError(f"Desteklenmeyen GStreamer video kaynağı: {source_type}")
        return (
            f"{source} ! videoconvert ! video/x-raw,format=RGBA "
            "! appsink name=yki_sink emit-signals=true sync=false max-buffers=2 drop=true"
        )

    @staticmethod
    def _udp_port(uri: str) -> int:
        if uri.isdigit():
            port = int(uri)
        else:
            parsed = urlparse(uri if "://" in uri else f"udp://{uri}")
            port = int(parsed.port or 0)
        if not 1 <= port <= 65535:
            raise ValueError(f"Geçersiz UDP video portu: {uri}")
        return port

    @staticmethod
    def _quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._logger:
            self._logger.event(action, message, severity=severity, details=details)
