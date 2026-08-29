from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
import ipaddress
import subprocess
from urllib.parse import urlparse
from typing import Any

from src.core.events import EventBus, Subscription
from src.models.video import VideoFrame, VideoShareConfig, VideoShareState
from src.services.logging_service import ServiceLogger


def active_ipv4_interfaces() -> tuple[tuple[str, str], ...]:
    """Return usable local IPv4 interfaces as ``(name, address)`` pairs."""

    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show", "up"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    interfaces: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[2] != "inet":
            continue
        name = fields[1].split("@", 1)[0]
        address = fields[3].split("/", 1)[0]
        if name != "lo" and (name, address) not in interfaces:
            interfaces.append((name, address))
    return tuple(interfaces)


class VideoSharePublisher:
    """Republish the camera frames supplied by Video Link.

    Each target owns a small GStreamer ``appsrc`` pipeline.  In fixed-frame
    mode its worker repeats the newest camera frame at the configured cadence;
    transient transport/capture drops therefore cannot change the published
    FPS.  This class deliberately has no screen-capture input.
    """

    def __init__(
        self,
        frames: EventBus[VideoFrame],
        logger: ServiceLogger | None = None,
    ) -> None:
        self._logger = logger
        self._configs: tuple[VideoShareConfig, ...] = ()
        self._states: dict[str, VideoShareState] = {}
        self._states_changed = EventBus[tuple[VideoShareState, ...]]()
        self._frames_lock = threading.RLock()
        self._latest_frame: VideoFrame | None = None
        self._frame_event = threading.Event()
        self._stop = threading.Event()
        self._workers: dict[str, threading.Thread] = {}
        self._server_time: Callable[[], datetime | None] = lambda: None
        self._subscription: Subscription = frames.subscribe(self._on_frame)

    @property
    def states_changed(self) -> EventBus[tuple[VideoShareState, ...]]:
        return self._states_changed

    @property
    def states(self) -> tuple[VideoShareState, ...]:
        return tuple(self._states.get(item.identifier, VideoShareState(item.identifier)) for item in self._configs)

    def set_server_time_provider(self, provider: Callable[[], datetime | None]) -> None:
        self._server_time = provider

    def configure(self, configs: tuple[VideoShareConfig, ...]) -> None:
        if configs == self._configs:
            return
        self.stop()
        self._configs = tuple(configs)
        self._states = {
            config.identifier: VideoShareState(
                config.identifier,
                status="waiting" if config.enabled else "inactive",
                detail="Kamera karesi bekleniyor" if config.enabled else "Yayın devre dışı",
            )
            for config in self._configs
        }
        self._publish_states()
        self._stop.clear()
        self._frame_event.clear()
        for config in self._configs:
            if not config.enabled:
                continue
            worker = threading.Thread(
                target=self._run_target,
                args=(config,),
                name=f"VideoShare[{config.identifier[:8]}]",
                daemon=True,
            )
            self._workers[config.identifier] = worker
            worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._frame_event.set()
        for worker in tuple(self._workers.values()):
            if worker is not threading.current_thread():
                worker.join(timeout=1.0)
        self._workers.clear()
        if self._configs:
            self._states = {
                config.identifier: VideoShareState(config.identifier, status="inactive", detail="Yayın durduruldu")
                for config in self._configs
            }
            self._publish_states()

    def close(self) -> None:
        self.stop()
        self._subscription.cancel()

    def _on_frame(self, frame: VideoFrame) -> None:
        with self._frames_lock:
            self._latest_frame = frame
        self._frame_event.set()

    def _run_target(self, config: VideoShareConfig) -> None:
        pipeline: Any | None = None
        try:
            if not self._frame_event.wait(10.0):
                self._set_state(config.identifier, "waiting", "Kamera karesi bekleniyor")
                while not self._stop.is_set() and not self._frame_event.wait(1.0):
                    pass
            if self._stop.is_set():
                return
            frame = self._current_frame()
            if frame is None:
                return
            if frame.width < 640 or frame.height < 480:
                raise RuntimeError(
                    f"Kaynak çözünürlük şartname altı: {frame.width}x{frame.height}"
                )
            clock_reference: datetime | None = None
            clock_started_at: float | None = None
            if config.timestamp_enabled:
                # A published timestamp must always be rooted in a real
                # TEKNOFEST sample.  Once received, advance that exact value
                # with monotonic time; do not transform it through the GCS
                # machine's local clock or timezone.
                clock_reference, clock_started_at = self._wait_for_clock_reference(
                    config.identifier
                )
                if clock_reference is None or clock_started_at is None:
                    return
            Gst = self._load_gstreamer()
            pipeline = Gst.parse_launch(self._pipeline_description(config, frame))
            appsrc = pipeline.get_by_name("yki_share_src")
            clock = pipeline.get_by_name("yki_share_clock")
            if appsrc is None:
                raise RuntimeError("Video paylaşım pipeline appsrc öğesi üretmedi")
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Video paylaşım pipeline PLAYING durumuna geçemedi")
            compliance = []
            if not config.fixed_frame_rate:
                compliance.append("sabit FPS kapalı")
            if not config.timestamp_enabled:
                compliance.append("sunucu saati kapalı")
            detail = f"{config.codec.upper()} · {config.fps} FPS"
            if compliance:
                detail += " · " + ", ".join(compliance)
            self._set_state(config.identifier, "active", detail)
            frame_duration = int(Gst.SECOND / config.fps)
            next_at = time.monotonic()
            sequence = 0
            previous: VideoFrame | None = None
            while not self._stop.is_set():
                if config.fixed_frame_rate:
                    next_at += 1.0 / config.fps
                    delay = max(0.0, next_at - time.monotonic())
                    self._frame_event.wait(delay)
                    self._frame_event.clear()
                else:
                    self._frame_event.wait(0.5)
                    self._frame_event.clear()
                frame = self._current_frame() or previous
                if frame is None:
                    continue
                if (frame.width, frame.height) != (previous.width, previous.height) if previous else False:
                    raise RuntimeError("Yayın sırasında çözünürlük değişti; paylaşım yeniden başlatılmalı")
                if clock is not None:
                    clock.set_property(
                        "text",
                        self._clock_text(clock_reference, clock_started_at)
                        if config.timestamp_enabled
                        else "",
                    )
                buffer = Gst.Buffer.new_allocate(None, len(frame.data), None)
                buffer.fill(0, frame.data)
                buffer.pts = sequence * frame_duration
                buffer.dts = buffer.pts
                buffer.duration = frame_duration
                if appsrc.emit("push-buffer", buffer) != Gst.FlowReturn.OK:
                    raise RuntimeError("Video paylaşım karesi hedefe iletilemedi")
                previous = frame
                sequence += 1
        except Exception as exc:
            self._set_state(config.identifier, "error", str(exc))
            self._event("video.share_failed", f"Video paylaşımı başlatılamadı: {config.name}: {exc}", "danger", {"share_id": config.identifier})
        finally:
            if pipeline is not None:
                try:
                    pipeline.set_state(self._load_gstreamer().State.NULL)
                except Exception:
                    pass

    def _current_frame(self) -> VideoFrame | None:
        with self._frames_lock:
            return self._latest_frame

    def _wait_for_clock_reference(
        self,
        identifier: str,
    ) -> tuple[datetime | None, float | None]:
        """Wait for the newest competition-clock sample before publishing."""

        while not self._stop.is_set():
            timestamp = self._server_time()
            if timestamp is not None:
                return timestamp, time.monotonic()
            self._set_state(identifier, "waiting", "TEKNOFEST sunucu saati bekleniyor")
            self._stop.wait(0.2)
        return None, None

    def _pipeline_description(self, config: VideoShareConfig, frame: VideoFrame) -> str:
        h264 = config.codec in {"h264", "h264-baseline"}
        if h264:
            encoder = (
                f"x264enc tune=zerolatency speed-preset=ultrafast "
                f"key-int-max={config.fps} bframes=0 byte-stream=true "
                "! video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au"
            )
            rtp_payloader = "rtph264pay pt=96 config-interval=1"
        else:
            encoder = (
                f"x265enc tune=zerolatency speed-preset=ultrafast "
                f"key-int-max={config.fps} ! video/x-h265,stream-format=byte-stream,alignment=au"
            )
            rtp_payloader = "rtph265pay pt=96 config-interval=1"
        sink = self._sink_description(config, rtp_payloader)
        return (
            f"appsrc name=yki_share_src is-live=true block=true format=time "
            f"caps=video/x-raw,format=RGBA,width={frame.width},height={frame.height},framerate={config.fps}/1 "
            "! videoconvert ! video/x-raw,format=I420 ! textoverlay name=yki_share_clock valignment=top halignment=right "
            "font-desc=Sans\\ 18 shaded-background=true ! "
            f"{encoder} ! {sink}"
        )

    @staticmethod
    def _sink_description(config: VideoShareConfig, rtp_payloader: str) -> str:
        if config.connection_type == "udp":
            parsed = urlparse(config.endpoint if "://" in config.endpoint else f"udp://{config.endpoint}")
            if not parsed.hostname or not parsed.port:
                raise ValueError("UDP video paylaşım adresi host:port biçiminde olmalı")
            options = ""
            try:
                is_multicast = ipaddress.ip_address(parsed.hostname).is_multicast
            except ValueError:
                is_multicast = False
            if is_multicast and config.network_interface:
                address = dict(active_ipv4_interfaces()).get(config.network_interface)
                if address is None:
                    raise RuntimeError(
                        f"Seçilen multicast ağ arayüzü aktif değil: {config.network_interface}"
                    )
                options = (
                    f" bind-address={address}"
                    f" multicast-iface={config.network_interface}"
                )
            return (
                "mpegtsmux alignment=7 ! "
                f"udpsink host={parsed.hostname} port={parsed.port}"
                f"{options} sync=false async=false"
            )
        if config.connection_type == "srt":
            return f"mpegtsmux ! srtsink uri=\"{config.endpoint}\""
        if config.connection_type == "rtmp":
            return f"flvmux streamable=true ! rtmpsink location=\"{config.endpoint}\""
        return f"{rtp_payloader} ! rtspclientsink location=\"{config.endpoint}\""

    @staticmethod
    def _load_gstreamer() -> Any:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as exc:
            raise RuntimeError("GStreamer Python bağları bulunamadı") from exc
        Gst.init(None)
        return Gst

    @staticmethod
    def _clock_text(reference: datetime, started_at: float) -> str:
        """Format the server-clock timeline without a local-time conversion."""

        value = reference + timedelta(seconds=max(0.0, time.monotonic() - started_at))
        return value.strftime("%H:%M:%S.") + f"{value.microsecond // 1000:03d}"

    def _set_state(self, identifier: str, status: str, detail: str) -> None:
        active = status == "active"
        state = VideoShareState(identifier, active=active, status=status, detail=detail)
        if self._states.get(identifier) == state:
            return
        self._states[identifier] = state
        self._publish_states()

    def _publish_states(self) -> None:
        self._states_changed.publish(self.states)

    def _event(self, action: str, message: str, severity: str, details: dict[str, object]) -> None:
        if self._logger:
            self._logger.event(action, message, severity=severity, details=details)
