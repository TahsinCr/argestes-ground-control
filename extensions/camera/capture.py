from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from src.core.events import EventBus, Subscription
from collections.abc import Callable
from datetime import datetime

from extensions.camera.models import (
    VideoFrame,
    VideoShareConfig,
    VideoShareState,
    VideoSourceConfig,
    VideoSourceFrame,
    VideoSourceStats,
    VideoStreamStats,
)
from extensions.camera.contracts import VideoService
from src.services.logging_service import ServiceLogger
from extensions.camera.publishing import VideoSharePublisher


class MutableVideoService(VideoService):
    def __init__(self, logger: ServiceLogger | None = None) -> None:
        self._frames = EventBus[VideoFrame]()
        self._stats_changed = EventBus[VideoStreamStats]()
        self._source_frames = EventBus[VideoSourceFrame]()
        self._source_stats_changed = EventBus[VideoSourceStats]()
        self._connection_changed = EventBus[bool]()
        self._logger = logger
        self._connected = False
        self._connection_lock = threading.RLock()
        self._stats = VideoStreamStats()
        self._stats_lock = threading.RLock()
        self._share_publisher = VideoSharePublisher(self._frames, logger)
        self._source_identifier = "active"

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

    @property
    def source_frames(self) -> EventBus[VideoSourceFrame]:
        return self._source_frames

    @property
    def source_stats_changed(self) -> EventBus[VideoSourceStats]:
        return self._source_stats_changed

    @property
    def source_stats(self) -> tuple[VideoSourceStats, ...]:
        return (VideoSourceStats(self._source_identifier, self.stats),)

    @property
    def share_states_changed(self) -> EventBus[tuple[VideoShareState, ...]]:
        return self._share_publisher.states_changed

    @property
    def share_states(self) -> tuple[VideoShareState, ...]:
        return self._share_publisher.states

    def configure_shares(self, configs: tuple[VideoShareConfig, ...]) -> None:
        self._share_publisher.configure(configs)

    def configure_sources(
        self,
        configs: tuple[VideoSourceConfig, ...],
        active_identifier: str,
    ) -> None:
        selected = next(
            (item for item in configs if item.identifier == active_identifier),
            configs[0],
        )
        self._source_identifier = selected.identifier
        self.configure(
            source_type=selected.source_type,
            source_uri=selected.source_uri,
            latency_ms=selected.latency_ms,
            codec=selected.codec,
            frame_timeout_s=selected.frame_timeout_s,
        )

    def set_active_source(self, identifier: str) -> None:
        self._source_identifier = str(identifier)

    def set_server_time_provider(self, provider: Callable[[], datetime | None]) -> None:
        self.set_clock_provider(provider)

    def set_clock_provider(self, provider: Callable[[], datetime | None]) -> None:
        self._share_publisher.set_clock_provider(provider)

    def start(self) -> None:
        self._set_connected(True)

    def stop(self) -> None:
        self._share_publisher.stop()
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
        self._source_frames.publish(VideoSourceFrame(self._source_identifier, frame))
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
        self._source_stats_changed.publish(
            VideoSourceStats(self._source_identifier, stats)
        )


class GStreamerVideoService(MutableVideoService):
    """Convert GStreamer appsink frames into the shared VideoFrame model."""

    _RESOLUTION_VERIFY_INTERVAL_S = 5.0
    _STATS_PUBLISH_INTERVAL_S = 1.0
    _FPS_WINDOW_S = 2.0

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
        self._last_frame_pipeline_age_s = 0.0
        self._last_stats_at = 0.0
        self._target_fps: float | None = None
        self._frame_times: deque[float] = deque()
        self._transport_samples: deque[tuple[float, int]] = deque()
        self._measurement_lock = threading.RLock()
        self._active_resolution: tuple[int, int] | None = None
        self._resolution_publish_pending = False
        self._last_resolution_verify_at = 0.0
        self._sink_pad: Any | None = None
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
        sink_pad = sink.get_static_pad("sink")
        if sink_pad is not None:
            sink_pad.add_probe(
                Gst.PadProbeType.EVENT_DOWNSTREAM,
                self._on_sink_event,
            )
        transport_meter = pipeline.get_by_name("yki_transport_stats")
        if transport_meter is not None:
            transport_pad = transport_meter.get_static_pad("src")
            if transport_pad is not None:
                transport_pad.add_probe(
                    Gst.PadProbeType.BUFFER, self._on_transport_buffer
                )
        with self._measurement_lock:
            self._frame_times.clear()
            self._transport_samples.clear()
            self._active_resolution = None
            self._resolution_publish_pending = False
            self._last_resolution_verify_at = 0.0
        self._sink_pad = sink_pad
        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer pipeline PLAYING durumuna geçemedi")
        self._pipeline = pipeline
        self._stop_event.clear()
        self._last_frame_at = 0.0
        self._last_frame_pipeline_age_s = 0.0
        self._frame_count = 0
        self._last_stats_at = 0.0
        self._target_fps = None
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
            self._share_publisher.stop()
            return
        self._stop_event.set()
        self._share_publisher.stop()
        pipeline = self._pipeline
        self._pipeline = None
        self._sink_pad = None
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
            self._observe_resolution(width, height)
            try:
                fraction = tuple(structure.get_fraction("framerate"))
                if len(fraction) == 3:
                    success, numerator, denominator = fraction
                    if not success:
                        raise ValueError("framerate is unavailable")
                else:
                    numerator, denominator = fraction
                if int(numerator) > 0 and int(denominator) > 0:
                    self._target_fps = float(numerator) / float(denominator)
            except (AttributeError, TypeError, ValueError):
                pass
            buffer = sample.get_buffer()
            frame_pipeline_age_s = self._buffer_pipeline_age_s(buffer)
            success, info = buffer.map(Gst.MapFlags.READ)
            if not success:
                return Gst.FlowReturn.ERROR
            try:
                data = bytes(info.data)
            finally:
                buffer.unmap(info)
            was_connected = self.connected
            self._last_frame_at = time.monotonic()
            self._last_frame_pipeline_age_s = frame_pipeline_age_s
            frame = VideoFrame(
                width=width,
                height=height,
                data=data,
                pixel_format="rgba8888",
                stride=width * 4,
            )
            self._set_connected(True)
            self._frames.publish(frame)
            self._source_frames.publish(
                VideoSourceFrame(self._source_identifier, frame)
            )
            self._frame_count += 1
            self._record_frame_stats(
                width,
                height,
                self._last_frame_at,
                frame_pipeline_age_s,
            )
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

    def _on_sink_event(self, _pad: Any, info: Any) -> Any:
        Gst = self._gst
        if Gst is None:
            return 0
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.CAPS:
            resolution = self._caps_resolution(event.parse_caps())
            if resolution is not None:
                self._observe_resolution(*resolution)
        return Gst.PadProbeReturn.OK

    @staticmethod
    def _caps_resolution(caps: Any) -> tuple[int, int] | None:
        if caps is None or caps.get_size() <= 0:
            return None
        structure = caps.get_structure(0)
        try:
            width = int(structure.get_value("width"))
            height = int(structure.get_value("height"))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

    def _observe_resolution(self, width: int, height: int) -> bool:
        resolution = (int(width), int(height))
        if resolution[0] <= 0 or resolution[1] <= 0:
            return False
        with self._measurement_lock:
            previous = self._active_resolution
            if previous == resolution:
                return False
            self._active_resolution = resolution
            self._resolution_publish_pending = True
        if previous is not None:
            self._event(
                "video.resolution_changed",
                (
                    f"Video çözünürlüğü {previous[0]}x{previous[1]} "
                    f"→ {resolution[0]}x{resolution[1]} olarak değişti"
                ),
                severity="success",
                details={
                    "previous_width": previous[0],
                    "previous_height": previous[1],
                    "width": resolution[0],
                    "height": resolution[1],
                },
            )
        return True

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

    def _buffer_pipeline_age_s(self, buffer: Any) -> float:
        Gst = self._gst
        pipeline = self._pipeline
        if Gst is None or pipeline is None:
            return 0.0
        pts = int(getattr(buffer, "pts", Gst.CLOCK_TIME_NONE))
        if pts == int(Gst.CLOCK_TIME_NONE):
            return 0.0
        clock = pipeline.get_clock()
        if clock is None:
            return 0.0
        base_time = int(pipeline.get_base_time())
        clock_time = int(clock.get_time())
        if base_time <= 0 or clock_time < base_time:
            return 0.0
        running_time = clock_time - base_time
        if running_time < pts:
            return 0.0
        return min(60.0, (running_time - pts) / float(Gst.SECOND))

    def _record_frame_stats(
        self,
        width: int,
        height: int,
        now: float,
        frame_age_s: float = 0.0,
    ) -> None:
        with self._measurement_lock:
            self._frame_times.append(now)
            cutoff = now - self._FPS_WINDOW_S
            while self._frame_times and self._frame_times[0] < cutoff:
                self._frame_times.popleft()
            while self._transport_samples and self._transport_samples[0][0] < cutoff:
                self._transport_samples.popleft()
            resolution_changed = self._resolution_publish_pending
            if (
                not resolution_changed
                and now - self._last_stats_at < self._STATS_PUBLISH_INTERVAL_S
                and self._frame_count > 1
            ):
                return
            self._last_stats_at = now
            self._resolution_publish_pending = False
            frame_times = tuple(self._frame_times)
            transport_samples = tuple(self._transport_samples)
        fps = 0.0
        if len(frame_times) >= 2:
            span = frame_times[-1] - frame_times[0]
            if span > 0.0:
                fps = (len(frame_times) - 1) / span
        target_fps = max(self._target_fps or fps, 1.0)
        delivery_score = min(1.0, fps / target_fps) if fps > 0.0 else 1.0
        jitter_score = 1.0
        if len(frame_times) >= 4:
            intervals = [
                later - earlier for earlier, later in zip(frame_times, frame_times[1:])
            ]
            mean_interval = sum(intervals) / len(intervals)
            if mean_interval > 0.0:
                variance = sum(
                    (interval - mean_interval) ** 2 for interval in intervals
                ) / len(intervals)
                jitter_ratio = variance**0.5 / mean_interval
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
                frame_age_s=max(0.0, float(frame_age_s)),
            )
        )

    def _watch_bus(self) -> None:
        pipeline = self._pipeline
        Gst = self._gst
        if pipeline is None or Gst is None:
            return
        bus = pipeline.get_bus()
        mask = (
            Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.STATE_CHANGED
        )
        while not self._stop_event.is_set():
            message = bus.timed_pop_filtered(250 * Gst.MSECOND, mask)
            if message is None:
                self._verify_negotiated_resolution()
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
                self._event(
                    "video.end_of_stream", "Video akışı sona erdi", severity="warning"
                )
                self._set_connected(False)
                self._stop_event.set()
            self._verify_negotiated_resolution()
            self._check_frame_timeout()

    def _verify_negotiated_resolution(self, now: float | None = None) -> None:
        checked_at = time.monotonic() if now is None else float(now)
        with self._measurement_lock:
            if (
                checked_at - self._last_resolution_verify_at
                < self._RESOLUTION_VERIFY_INTERVAL_S
            ):
                return
            self._last_resolution_verify_at = checked_at
        sink_pad = self._sink_pad
        if sink_pad is None:
            return
        resolution = self._caps_resolution(sink_pad.get_current_caps())
        if resolution is not None:
            self._observe_resolution(*resolution)

    def _check_frame_timeout(self, now: float | None = None) -> None:
        if not self.connected or self._last_frame_at <= 0:
            return
        elapsed = (
            time.monotonic() if now is None else float(now)
        ) - self._last_frame_at
        current = self.stats
        measured_age = self._last_frame_pipeline_age_s + max(0.0, elapsed)
        if (
            current.frame_age_s is None
            or abs(measured_age - current.frame_age_s) >= 0.05
        ):
            self._publish_stats(
                VideoStreamStats(
                    connected=current.connected,
                    quality_percent=current.quality_percent,
                    frames_received=current.frames_received,
                    fps=current.fps,
                    bitrate_kbps=current.bitrate_kbps,
                    width=current.width,
                    height=current.height,
                    frame_age_s=measured_age,
                )
            )
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
            Path.home()
            / ".local"
            / "share"
            / "argestes-ground-station"
            / "gstreamer-1.0"
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


class MultiVideoService(VideoService):
    """Own one capture pipeline per configured input and expose one focused feed.

    Legacy consumers keep receiving ``frames`` and ``stats`` for the focused
    source. Multi-camera consumers use the source-tagged event channels.
    """

    def __init__(
        self,
        configs: tuple[VideoSourceConfig, ...],
        active_identifier: str,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._logger = logger
        self._frames = EventBus[VideoFrame]()
        self._stats_changed = EventBus[VideoStreamStats]()
        self._connection_changed = EventBus[bool]()
        self._source_frames = EventBus[VideoSourceFrame]()
        self._source_stats_changed = EventBus[VideoSourceStats]()
        self._configs: dict[str, VideoSourceConfig] = {}
        self._share_configs: tuple[VideoShareConfig, ...] = ()
        self._sources: dict[str, MutableVideoService] = {}
        self._source_subscriptions: dict[str, list[Subscription]] = {}
        self._active_identifier = str(active_identifier)
        self._started = False
        self._manual_source_ids: set[str] = set()
        self._lock = threading.RLock()
        self._share_publisher = VideoSharePublisher(
            self._frames,
            logger,
            source_frames=self._source_frames,
        )
        self._share_publisher.set_active_source_provider(
            lambda: self._active_identifier
        )
        self.configure_sources(configs, active_identifier)

    @property
    def connected(self) -> bool:
        source = self._sources.get(self._active_identifier)
        return bool(source and source.connected)

    @property
    def connection_changed(self) -> EventBus[bool]:
        return self._connection_changed

    @property
    def frames(self) -> EventBus[VideoFrame]:
        return self._frames

    @property
    def stats(self) -> VideoStreamStats:
        source = self._sources.get(self._active_identifier)
        return source.stats if source is not None else VideoStreamStats()

    @property
    def stats_changed(self) -> EventBus[VideoStreamStats]:
        return self._stats_changed

    @property
    def source_frames(self) -> EventBus[VideoSourceFrame]:
        return self._source_frames

    @property
    def source_stats_changed(self) -> EventBus[VideoSourceStats]:
        return self._source_stats_changed

    @property
    def source_stats(self) -> tuple[VideoSourceStats, ...]:
        return tuple(
            VideoSourceStats(identifier, source.stats)
            for identifier, source in self._sources.items()
        )

    @property
    def share_states_changed(self) -> EventBus[tuple[VideoShareState, ...]]:
        return self._share_publisher.states_changed

    @property
    def share_states(self) -> tuple[VideoShareState, ...]:
        return self._share_publisher.states

    def configure_shares(self, configs: tuple[VideoShareConfig, ...]) -> None:
        self._share_configs = tuple(configs)
        self._share_publisher.configure(configs)
        if self._started:
            with self._lock:
                self._reconcile_running_sources()

    def set_server_time_provider(self, provider: Callable[[], datetime | None]) -> None:
        self.set_clock_provider(provider)

    def set_clock_provider(self, provider: Callable[[], datetime | None]) -> None:
        self._share_publisher.set_clock_provider(provider)

    def configure_sources(
        self,
        configs: tuple[VideoSourceConfig, ...],
        active_identifier: str,
    ) -> None:
        if not configs:
            raise ValueError("En az bir video bağlantısı gerekli")
        next_configs = {item.identifier: item for item in configs}
        selected = (
            active_identifier
            if active_identifier in next_configs
            else configs[0].identifier
        )
        with self._lock:
            previous_active = self._active_identifier
            removed_or_changed = {
                identifier
                for identifier, old in self._configs.items()
                if identifier not in next_configs
                or self._capture_signature(next_configs[identifier])
                != self._capture_signature(old)
            }
            for identifier in removed_or_changed:
                self._remove_source(identifier)
            self._configs = next_configs
            for config in configs:
                if config.identifier not in self._sources:
                    self._add_source(config)
            self._active_identifier = selected
            if self._started:
                self._reconcile_running_sources()
        self._publish_focused_state()
        if previous_active != selected:
            self._share_publisher.restart()

    def set_active_source(self, identifier: str) -> None:
        if identifier not in self._sources:
            raise ValueError(f"Bilinmeyen video bağlantısı: {identifier}")
        with self._lock:
            previous = self.connected
            self._active_identifier = identifier
            if self._started:
                self._manual_source_ids.add(identifier)
                self._start_source(identifier)
            current = self.connected
        self._stats_changed.publish(self.stats)
        if previous != current:
            self._connection_changed.publish(current)

    def configure(
        self,
        *,
        source_type: str,
        source_uri: str,
        latency_ms: int,
        codec: str,
        frame_timeout_s: float,
    ) -> None:
        current = self._configs[self._active_identifier]
        updated = VideoSourceConfig(
            identifier=current.identifier,
            name=current.name,
            source_type=source_type,
            source_uri=source_uri,
            latency_ms=latency_ms,
            codec=codec,
            frame_timeout_s=frame_timeout_s,
            auto_connect=current.auto_connect,
        )
        configs = tuple(
            updated if item.identifier == updated.identifier else item
            for item in self._configs.values()
        )
        self.configure_sources(configs, updated.identifier)

    def start(self) -> None:
        with self._lock:
            self._started = True
            self._reconcile_running_sources()

    def stop(self) -> None:
        with self._lock:
            self._started = False
            self._manual_source_ids.clear()
            for source in self._sources.values():
                source.stop()
        self._share_publisher.stop()
        self._publish_focused_state()

    def close(self, *, timeout_s: float = 3.5) -> bool:
        """Permanently close capture and publisher resources owned by this service."""

        self.stop()
        closed = self._share_publisher.close(timeout_s=timeout_s)
        with self._lock:
            for source in self._sources.values():
                source.stop()
            for subscriptions in self._source_subscriptions.values():
                for subscription in subscriptions:
                    subscription.cancel()
            self._source_subscriptions.clear()
        return closed

    def reconnect(self) -> None:
        source = self._sources.get(self._active_identifier)
        if source is None:
            return
        self._started = True
        self._manual_source_ids.add(self._active_identifier)
        source.reconnect()
        with self._lock:
            self._reconcile_running_sources()

    def push(self, frame: VideoFrame) -> None:
        source = self._sources.get(self._active_identifier)
        if source is not None:
            source.push(frame)

    def _add_source(self, config: VideoSourceConfig) -> None:
        if config.source_type == "mock":
            source: MutableVideoService = MutableVideoService(self._logger)
        else:
            source = GStreamerVideoService(
                config.source_type,
                config.source_uri,
                self._logger,
                latency_ms=config.latency_ms,
                codec=config.codec,
                frame_timeout_s=config.frame_timeout_s,
            )
        source.set_active_source(config.identifier)
        subscriptions = [
            source.frames.subscribe(
                lambda frame, source_id=config.identifier: self._on_source_frame(
                    source_id, frame
                )
            ),
            source.stats_changed.subscribe(
                lambda stats, source_id=config.identifier: self._on_source_stats(
                    source_id, stats
                )
            ),
        ]
        self._sources[config.identifier] = source
        self._source_subscriptions[config.identifier] = subscriptions

    def _remove_source(self, identifier: str) -> None:
        self._manual_source_ids.discard(identifier)
        source = self._sources.pop(identifier, None)
        if source is not None:
            source.stop()
        for subscription in self._source_subscriptions.pop(identifier, ()):
            subscription.cancel()
        self._share_publisher.remove_source(identifier)

    def _reconcile_running_sources(self) -> None:
        desired = {
            identifier
            for identifier, config in self._configs.items()
            if config.auto_connect
        }
        desired.update(self._manual_source_ids)
        desired.update(
            config.source_id
            for config in self._share_configs
            if config.enabled and config.source_id != "panel"
        )
        if any(
            config.enabled and config.source_id == "panel"
            for config in self._share_configs
        ):
            desired.add(self._active_identifier)
        for identifier, source in self._sources.items():
            if identifier in desired:
                self._start_source(identifier)
            else:
                source.stop()

    def _start_source(self, identifier: str) -> None:
        source = self._sources[identifier]
        try:
            source.start()
        except Exception as exc:
            if self._logger:
                self._logger.event(
                    "video.source_start_failed",
                    f"Video bağlantısı başlatılamadı: {self._configs[identifier].name}: {exc}",
                    severity="danger",
                    details={"source_id": identifier},
                )

    def _on_source_frame(self, identifier: str, frame: VideoFrame) -> None:
        self._source_frames.publish(VideoSourceFrame(identifier, frame))
        if identifier == self._active_identifier:
            self._frames.publish(frame)

    def _on_source_stats(self, identifier: str, stats: VideoStreamStats) -> None:
        self._source_stats_changed.publish(VideoSourceStats(identifier, stats))
        if identifier == self._active_identifier:
            self._stats_changed.publish(stats)
            self._connection_changed.publish(stats.connected)

    def _publish_focused_state(self) -> None:
        self._stats_changed.publish(self.stats)
        self._connection_changed.publish(self.connected)

    @staticmethod
    def _capture_signature(config: VideoSourceConfig) -> tuple[object, ...]:
        return (
            config.source_type,
            config.source_uri,
            config.codec,
            config.latency_ms,
            config.frame_timeout_s,
        )
