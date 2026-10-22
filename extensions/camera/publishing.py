from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
import ipaddress
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
from typing import Any

from src.core.events import EventBus, Subscription
from extensions.camera.models import (
    VideoFrame,
    VideoShareConfig,
    VideoShareState,
    VideoSourceFrame,
)
from src.services.logging_service import ServiceLogger


_INTERFACE_CACHE_TTL_S = 5.0
_interface_cache_lock = threading.Lock()
_interface_cache: tuple[tuple[str, str], ...] = ()
_interface_cache_at = 0.0
_interface_refreshing = False
_RECORDING_FRAME_STALE_S = 1.0


def _read_active_ipv4_interfaces() -> tuple[tuple[str, str], ...]:
    """Read usable local IPv4 interfaces; call only off the GUI thread."""

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


def refresh_active_ipv4_interfaces() -> tuple[tuple[str, str], ...]:
    """Refresh the local interface catalogue for transport workers."""

    global _interface_cache, _interface_cache_at, _interface_refreshing
    interfaces = _read_active_ipv4_interfaces()
    with _interface_cache_lock:
        _interface_cache = interfaces
        _interface_cache_at = time.monotonic()
        _interface_refreshing = False
        return _interface_cache


def active_ipv4_interfaces() -> tuple[tuple[str, str], ...]:
    """Return cached interfaces immediately and refresh stale data in background."""

    global _interface_refreshing
    with _interface_cache_lock:
        interfaces = _interface_cache
        stale = time.monotonic() - _interface_cache_at >= _INTERFACE_CACHE_TTL_S
        if stale and not _interface_refreshing:
            _interface_refreshing = True
            threading.Thread(
                target=refresh_active_ipv4_interfaces,
                name="ActiveIpv4Interfaces",
                daemon=True,
            ).start()
        return interfaces


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
        *,
        source_frames: EventBus[VideoSourceFrame] | None = None,
    ) -> None:
        self._logger = logger
        self._configs: tuple[VideoShareConfig, ...] = ()
        self._states: dict[str, VideoShareState] = {}
        self._states_changed = EventBus[tuple[VideoShareState, ...]]()
        self._frames_lock = threading.RLock()
        self._latest_frame: VideoFrame | None = None
        self._latest_source_frames: dict[str, VideoFrame] = {}
        self._latest_frame_at = 0.0
        self._latest_source_frame_at: dict[str, float] = {}
        self._active_source: Callable[[], str] = lambda: ""
        self._frame_event = threading.Event()
        self._stop = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._workers: dict[str, threading.Thread] = {}
        self._server_time: Callable[[], datetime | None] = lambda: None
        self._subscription: Subscription = frames.subscribe(self._on_frame)
        self._source_subscription = (
            source_frames.subscribe(self._on_source_frame)
            if source_frames is not None
            else None
        )

    @property
    def states_changed(self) -> EventBus[tuple[VideoShareState, ...]]:
        return self._states_changed

    @property
    def states(self) -> tuple[VideoShareState, ...]:
        with self._lifecycle_lock:
            return tuple(
                self._states.get(item.identifier, VideoShareState(item.identifier))
                for item in self._configs
            )

    def set_server_time_provider(self, provider: Callable[[], datetime | None]) -> None:
        self._server_time = provider

    def set_active_source_provider(self, provider: Callable[[], str]) -> None:
        self._active_source = provider

    def configure(self, configs: tuple[VideoShareConfig, ...]) -> None:
        if configs == self._configs:
            return
        self.stop()
        with self._lifecycle_lock:
            self._configs = tuple(configs)
            self._states = {
                config.identifier: VideoShareState(
                    config.identifier,
                    status="waiting" if config.enabled else "inactive",
                    detail="Kamera karesi bekleniyor"
                    if config.enabled
                    else (
                        "Kayıt devre dışı"
                        if config.output_type == "record"
                        else "Yayın devre dışı"
                    ),
                )
                for config in self._configs
            }
            self._stop = threading.Event()
            stop_event = self._stop
            self._frame_event.clear()
        self._publish_states()
        for config in self._configs:
            if not config.enabled:
                continue
            self._start_target(config, stop_event)

    def restart(self) -> None:
        """Restart configured targets after their dynamic panel source changes."""

        configs = self._configs
        if not configs:
            return
        with self._lifecycle_lock:
            self._configs = ()
        self.configure(configs)

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            self._frame_event.set()
            for config in self._configs:
                state = self._states.get(config.identifier)
                if config.enabled and state is not None and state.status != "error":
                    self._states[config.identifier] = VideoShareState(
                        config.identifier,
                        status="waiting",
                        detail="Çıkış akışı güvenle sonlandırılıyor",
                    )
            workers = tuple(self._workers.items())
            for identifier, worker in workers:
                if worker.is_alive():
                    continue
                self._workers.pop(identifier, None)
                config = next(
                    (item for item in self._configs if item.identifier == identifier),
                    None,
                )
                if config is not None:
                    self._states[identifier] = VideoShareState(
                        identifier,
                        status="inactive",
                        detail="Kayıt durduruldu"
                        if config.output_type == "record"
                        else "Yayın durduruldu",
                    )
        if self._configs:
            self._publish_states()

    def close(self) -> None:
        self.stop()
        deadline = time.monotonic() + 3.5
        current = threading.current_thread()
        with self._lifecycle_lock:
            workers = tuple(self._workers.values())
        for worker in workers:
            if worker is current:
                continue
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        self._subscription.cancel()
        if self._source_subscription is not None:
            self._source_subscription.cancel()

    def _start_target(
        self,
        config: VideoShareConfig,
        stop_event: threading.Event,
    ) -> None:
        with self._lifecycle_lock:
            if stop_event is not self._stop or stop_event.is_set():
                return
            existing = self._workers.get(config.identifier)
            if existing is not None and existing.is_alive():
                return
            worker = threading.Thread(
                target=self._run_target,
                args=(config, stop_event),
                name=f"VideoShare[{config.identifier[:8]}]",
                daemon=True,
            )
            self._workers[config.identifier] = worker
            worker.start()

    def _target_finished(
        self,
        identifier: str,
        worker: threading.Thread,
        stop_event: threading.Event,
    ) -> None:
        replacement: VideoShareConfig | None = None
        replacement_event: threading.Event | None = None
        publish = False
        with self._lifecycle_lock:
            if self._workers.get(identifier) is worker:
                self._workers.pop(identifier, None)
            desired = next(
                (item for item in self._configs if item.identifier == identifier),
                None,
            )
            current_event = self._stop
            if (
                desired is not None
                and desired.enabled
                and not current_event.is_set()
                and stop_event is not current_event
            ):
                replacement = desired
                replacement_event = current_event
            elif desired is None or not desired.enabled or current_event.is_set():
                state = self._states.get(identifier)
                if state is not None and state.status != "error":
                    self._states[identifier] = VideoShareState(
                        identifier,
                        status="inactive",
                        detail="Kayıt durduruldu"
                        if desired is None or desired.output_type == "record"
                        else "Yayın durduruldu",
                    )
                    publish = True
        if publish:
            self._publish_states()
        if replacement is not None and replacement_event is not None:
            self._start_target(replacement, replacement_event)

    def _on_frame(self, frame: VideoFrame) -> None:
        with self._frames_lock:
            self._latest_frame = frame
            self._latest_frame_at = time.monotonic()
        self._frame_event.set()

    def _on_source_frame(self, item: VideoSourceFrame) -> None:
        with self._frames_lock:
            self._latest_source_frames[item.source_id] = item.frame
            self._latest_source_frame_at[item.source_id] = time.monotonic()
        self._frame_event.set()

    def _run_target(
        self, config: VideoShareConfig, stop_event: threading.Event
    ) -> None:
        pipeline: Any | None = None
        appsrc: Any | None = None
        try:
            frame = self._wait_for_frame(config, stop_event)
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
                    config.identifier,
                    stop_event,
                )
                if clock_reference is None or clock_started_at is None:
                    return
            Gst = self._load_gstreamer()
            output_path = (
                self._recording_path(config) if config.output_type == "record" else None
            )
            encoder_chain, encoder_name = self._encoder_chain(Gst, config)
            active_pipeline = Gst.parse_launch(
                self._pipeline_description(
                    config,
                    frame,
                    encoder_chain=encoder_chain,
                    output_path=output_path,
                )
            )
            pipeline = active_pipeline
            appsrc = active_pipeline.get_by_name("yki_share_src")
            clock = active_pipeline.get_by_name("yki_share_clock")
            if appsrc is None:
                raise RuntimeError("Video paylaşım pipeline appsrc öğesi üretmedi")
            if (
                active_pipeline.set_state(Gst.State.PLAYING)
                == Gst.StateChangeReturn.FAILURE
            ):
                raise RuntimeError("Video paylaşım pipeline PLAYING durumuna geçemedi")
            compliance = []
            if not config.fixed_frame_rate:
                compliance.append("sabit FPS kapalı")
            if not config.timestamp_enabled:
                compliance.append("sunucu saati kapalı")
            detail = f"{config.codec.upper()} · {config.fps} FPS · {encoder_name}"
            if output_path is not None:
                detail += f" · {output_path.name}"
            if compliance:
                detail += " · " + ", ".join(compliance)
            self._set_state(config.identifier, "active", detail, stop_event=stop_event)
            frame_duration = int(Gst.SECOND / config.fps)
            next_at = time.monotonic()
            sequence = 0
            previous: VideoFrame | None = None
            while not stop_event.is_set():
                if config.fixed_frame_rate:
                    next_at += 1.0 / config.fps
                    while not stop_event.is_set():
                        delay = next_at - time.monotonic()
                        if delay <= 0.0:
                            break
                        stop_event.wait(min(delay, 0.1))
                    now = time.monotonic()
                    if next_at < now - (1.0 / config.fps):
                        next_at = now
                else:
                    self._frame_event.wait(0.5)
                    self._frame_event.clear()
                frame = self._current_frame(config)
                if config.output_type == "record" and not self._frame_is_fresh(config):
                    self._set_state(
                        config.identifier,
                        "waiting",
                        "Kamera bağlantısı bekleniyor; kayıt duraklatıldı",
                        stop_event=stop_event,
                    )
                    previous = None
                    next_at = time.monotonic()
                    continue
                frame = frame or previous
                if frame is None:
                    continue
                if (
                    (frame.width, frame.height) != (previous.width, previous.height)
                    if previous
                    else False
                ):
                    raise RuntimeError(
                        "Yayın sırasında çözünürlük değişti; paylaşım yeniden başlatılmalı"
                    )
                if clock is not None:
                    if config.timestamp_enabled:
                        assert (
                            clock_reference is not None and clock_started_at is not None
                        )
                        clock.set_property(
                            "text",
                            self._clock_text(clock_reference, clock_started_at),
                        )
                    else:
                        clock.set_property("text", "")
                # Wrap the immutable frame bytes directly; allocating and
                # copying an RGBA buffer per camera at 30 FPS is prohibitively
                # expensive for multi-camera recording.
                buffer = Gst.Buffer.new_wrapped(frame.data)
                buffer.pts = sequence * frame_duration
                buffer.dts = buffer.pts
                buffer.duration = frame_duration
                if appsrc.emit("push-buffer", buffer) != Gst.FlowReturn.OK:
                    raise RuntimeError("Video paylaşım karesi hedefe iletilemedi")
                previous = frame
                sequence += 1
                if config.output_type == "record":
                    self._set_state(
                        config.identifier, "active", detail, stop_event=stop_event
                    )
        except Exception as exc:
            self._set_state(config.identifier, "error", str(exc), stop_event=stop_event)
            self._event(
                "video.share_failed",
                f"Video paylaşımı başlatılamadı: {config.name}: {exc}",
                "danger",
                {"share_id": config.identifier},
            )
        finally:
            if pipeline is not None:
                try:
                    Gst = self._load_gstreamer()
                    if config.output_type == "record":
                        if appsrc is not None:
                            appsrc.emit("end-of-stream")
                        else:
                            pipeline.send_event(Gst.Event.new_eos())
                        bus = pipeline.get_bus()
                        if bus is not None:
                            bus.timed_pop_filtered(
                                3 * Gst.SECOND,
                                Gst.MessageType.EOS | Gst.MessageType.ERROR,
                            )
                    pipeline.set_state(Gst.State.NULL)
                except Exception:
                    pass
            self._target_finished(
                config.identifier,
                threading.current_thread(),
                stop_event,
            )

    def _current_frame(self, config: VideoShareConfig) -> VideoFrame | None:
        with self._frames_lock:
            if config.source_id == "panel":
                return self._latest_source_frames.get(
                    self._active_source(),
                    self._latest_frame,
                )
            return self._latest_source_frames.get(config.source_id)

    def _frame_is_fresh(self, config: VideoShareConfig) -> bool:
        with self._frames_lock:
            received_at = (
                self._latest_frame_at
                if config.source_id == "panel"
                else self._latest_source_frame_at.get(config.source_id, 0.0)
            )
        return time.monotonic() - received_at <= _RECORDING_FRAME_STALE_S

    def _wait_for_frame(
        self, config: VideoShareConfig, stop_event: threading.Event
    ) -> VideoFrame | None:
        while not stop_event.is_set():
            frame = self._current_frame(config)
            if frame is not None:
                return frame
            source_name = (
                "panel" if config.source_id == "panel" else config.source_id[:12]
            )
            self._set_state(
                config.identifier,
                "waiting",
                f"Kamera karesi bekleniyor: {source_name}",
                stop_event=stop_event,
            )
            self._frame_event.wait(0.5)
            self._frame_event.clear()
        return None

    def _wait_for_clock_reference(
        self,
        identifier: str,
        stop_event: threading.Event,
    ) -> tuple[datetime | None, float | None]:
        """Wait for the newest competition-clock sample before publishing."""

        while not stop_event.is_set():
            timestamp = self._server_time()
            if timestamp is not None:
                return timestamp, time.monotonic()
            self._set_state(
                identifier,
                "waiting",
                "TEKNOFEST sunucu saati bekleniyor",
                stop_event=stop_event,
            )
            stop_event.wait(0.2)
        return None, None

    def _pipeline_description(
        self,
        config: VideoShareConfig,
        frame: VideoFrame,
        *,
        encoder_chain: str | None = None,
        output_path: Path | None = None,
    ) -> str:
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
        encoder = encoder_chain or encoder
        sink = self._sink_description(config, rtp_payloader, output_path=output_path)
        return (
            f"appsrc name=yki_share_src is-live=true block=false max-bytes={max(1, frame.width * frame.height * 4)} format=time "
            f"caps=video/x-raw,format=RGBA,width={frame.width},height={frame.height},framerate={config.fps}/1 "
            "! queue leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0 "
            "! videoconvert ! video/x-raw,format=I420 ! textoverlay name=yki_share_clock valignment=top halignment=right "
            "font-desc=Sans\\ 18 shaded-background=true ! "
            f"{encoder} ! {sink}"
        )

    @staticmethod
    def _sink_description(
        config: VideoShareConfig,
        rtp_payloader: str,
        *,
        output_path: Path | None = None,
    ) -> str:
        if config.output_type == "record":
            if output_path is None:
                raise ValueError("Kayıt dosyası yolu oluşturulmadı")
            parser = (
                "h264parse"
                if config.codec in {"h264", "h264-baseline"}
                else "h265parse"
            )
            location = str(output_path).replace("\\", "\\\\").replace('"', '\\"')
            return (
                f"{parser} ! mp4mux faststart=true ! "
                f'filesink location="{location}" sync=false async=false'
            )
        if config.connection_type == "udp":
            parsed = urlparse(
                config.endpoint
                if "://" in config.endpoint
                else f"udp://{config.endpoint}"
            )
            if not parsed.hostname or not parsed.port:
                raise ValueError("UDP video paylaşım adresi host:port biçiminde olmalı")
            options = ""
            try:
                is_multicast = ipaddress.ip_address(parsed.hostname).is_multicast
            except ValueError:
                is_multicast = False
            if is_multicast and config.network_interface:
                address = dict(refresh_active_ipv4_interfaces()).get(
                    config.network_interface
                )
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
            return f'mpegtsmux ! srtsink uri="{config.endpoint}"'
        if config.connection_type == "rtmp":
            return f'flvmux streamable=true ! rtmpsink location="{config.endpoint}"'
        return f'{rtp_payloader} ! rtspclientsink location="{config.endpoint}"'

    @staticmethod
    def _encoder_chain(Gst: Any, config: VideoShareConfig) -> tuple[str, str]:
        h264 = config.codec in {"h264", "h264-baseline"}
        hardware = (
            (
                "nvv4l2h264enc" if h264 else "nvv4l2h265enc",
                "nvvidconv ! video/x-raw(memory:NVMM),format=NV12 ! "
                + (
                    "nvv4l2h264enc insert-sps-pps=true iframeinterval=30"
                    if h264
                    else "nvv4l2h265enc iframeinterval=30"
                ),
                "NVIDIA NVENC",
            ),
            (
                "nvh264enc" if h264 else "nvh265enc",
                "videoconvert ! video/x-raw,format=NV12 ! "
                + (
                    "nvh264enc gop-size=30 bframes=0"
                    if h264
                    else "nvh265enc gop-size=30"
                ),
                "NVIDIA NVENC",
            ),
            (
                "vah264enc" if h264 else "vah265enc",
                "videoconvert ! video/x-raw,format=NV12 ! "
                + ("vah264enc key-int-max=30" if h264 else "vah265enc key-int-max=30"),
                "VA-API",
            ),
            (
                "vaapih264enc" if h264 else "vaapih265enc",
                "videoconvert ! video/x-raw,format=NV12 ! "
                + (
                    "vaapih264enc keyframe-period=30"
                    if h264
                    else "vaapih265enc keyframe-period=30"
                ),
                "VA-API",
            ),
        )
        for element, chain, label in hardware:
            if Gst.ElementFactory.find(element) is not None:
                return chain, label
        if config.output_type == "record":
            raise RuntimeError("Donanımsal H.264/H.265 video kodlayıcı bulunamadı")
        software = (
            "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 bframes=0 byte-stream=true"
            if h264
            else "x265enc tune=zerolatency speed-preset=ultrafast key-int-max=30"
        )
        return f"videoconvert ! video/x-raw,format=I420 ! {software}", "CPU"

    @staticmethod
    def _recording_path(config: VideoShareConfig) -> Path:
        directory = Path(config.endpoint or "data/recordings").expanduser()
        if not directory.is_absolute():
            directory = Path(__file__).resolve().parents[2] / directory
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", config.name).strip("_") or "camera"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return directory / f"{stamp}_{safe_name}.mp4"

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

    def _set_state(
        self,
        identifier: str,
        status: str,
        detail: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        with self._lifecycle_lock:
            if stop_event is not None and stop_event is not self._stop:
                return
            active = status == "active"
            state = VideoShareState(
                identifier, active=active, status=status, detail=detail
            )
            if self._states.get(identifier) == state:
                return
            self._states[identifier] = state
        self._publish_states()

    def _publish_states(self) -> None:
        self._states_changed.publish(self.states)

    def _event(
        self, action: str, message: str, severity: str, details: dict[str, object]
    ) -> None:
        if self._logger:
            self._logger.event(action, message, severity=severity, details=details)
