from __future__ import annotations

import shutil
import stat
import subprocess
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from src.core.events import EventStream
from src.models.settings import PhoneCameraSettings
from src.models.video import PhoneCameraBridgeState
from src.services.contracts.video import PhoneCameraBridgeService
from src.services.logging_service import ServiceLogger


class ScrcpyPhoneCameraBridge(PhoneCameraBridgeService):
    """ADB telefon kamerasını scrcpy ile bir v4l2loopback aygıtına aktarır."""

    def __init__(
        self,
        config: PhoneCameraSettings,
        logger: ServiceLogger | None = None,
        *,
        run_command: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self._config = config
        self._logger = logger
        self._run_command = run_command
        self._popen = popen
        self._changed = EventStream[PhoneCameraBridgeState]()
        self._state = PhoneCameraBridgeState(
            enabled=config.enabled,
            video_device=config.video_device,
        )
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._process: Any | None = None

    @property
    def state(self) -> PhoneCameraBridgeState:
        with self._state_lock:
            return self._state

    @property
    def changed(self) -> EventStream[PhoneCameraBridgeState]:
        return self._changed

    def configure(self, config: PhoneCameraSettings) -> None:
        with self._lifecycle_lock:
            changed = config != self._config
            was_started = self._monitor_thread is not None
        if changed and was_started:
            self.stop()
        with self._lifecycle_lock:
            self._config = config
        self._publish_state(
            enabled=config.enabled,
            video_device=config.video_device,
            last_error="",
        )
        if config.enabled and (changed and was_started):
            self.start()
        elif not config.enabled:
            self.stop()

    def start(self) -> None:
        with self._lifecycle_lock:
            if not self._config.enabled:
                self._publish_state(enabled=False, running=False)
                return
            if self._monitor_thread is not None:
                return
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor,
                name="GalaxyUsbCameraBridge",
                daemon=True,
            )
            self._monitor_thread.start()
        self._event(
            "phone_camera.bridge_started",
            "Samsung USB kamera köprüsü başlatıldı",
            details={"video_device": self._config.video_device},
        )

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            process = self._process
            thread = self._monitor_thread
            self._monitor_thread = None
        self._terminate_process(process)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._publish_state(running=False, process_id=None)

    def _monitor(self) -> None:
        last_error = ""
        while not self._stop_event.is_set():
            try:
                self._validate_dependencies()
                serial = self._resolve_adb_serial()
                self._validate_video_device()
                process = self._launch(serial)
                with self._lifecycle_lock:
                    self._process = process
                self._publish_state(
                    enabled=True,
                    running=True,
                    adb_connected=True,
                    video_device_ready=True,
                    serial=serial,
                    process_id=int(process.pid),
                    last_error="",
                )
                last_error = ""
                self._event(
                    "phone_camera.scrcpy_started",
                    "Samsung arka kamera /dev/video köprüsüne bağlandı",
                    severity="success",
                    details={
                        "serial": serial,
                        "video_device": self._config.video_device,
                        "pid": int(process.pid),
                        "resolution": f"{self._config.width}x{self._config.height}",
                        "fps": self._config.fps,
                    },
                )
                reader = threading.Thread(
                    target=self._read_process_output,
                    args=(process,),
                    name="ScrcpyCameraLog",
                    daemon=True,
                )
                reader.start()
                while process.poll() is None and not self._stop_event.wait(0.25):
                    pass
                if self._stop_event.is_set():
                    self._terminate_process(process)
                    return
                return_code = process.poll()
                raise RuntimeError(f"scrcpy kamera köprüsü kapandı (kod {return_code})")
            except Exception as exc:
                message = str(exc)
                self._publish_state(
                    running=False,
                    process_id=None,
                    adb_connected=self._adb_serial_is_ready(self._config.adb_serial),
                    video_device_ready=self._video_device_ready(),
                    last_error=message,
                )
                if message != last_error:
                    last_error = message
                    self._event(
                        "phone_camera.bridge_waiting",
                        f"Samsung USB kamera bekleniyor: {message}",
                        severity="warning",
                    )
            finally:
                with self._lifecycle_lock:
                    self._process = None
            self._stop_event.wait(float(self._config.restart_interval_s))

    def _validate_dependencies(self) -> None:
        missing = [name for name in ("adb", "scrcpy") if shutil.which(name) is None]
        if missing:
            raise FileNotFoundError("Eksik telefon kamera araçları: " + ", ".join(missing))

    def _resolve_adb_serial(self) -> str:
        result = self._run_command(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )
        devices = self.parse_adb_devices(result.stdout)
        configured = self._config.adb_serial
        if configured:
            state = devices.get(configured)
            if state != "device":
                raise ConnectionError(
                    f"ADB cihazı hazır değil: {configured} ({state or 'bulunamadı'})"
                )
            return configured
        ready = [serial for serial, state in devices.items() if state == "device"]
        if not ready:
            raise ConnectionError("Yetkilendirilmiş ADB telefonu bulunamadı")
        if len(ready) > 1:
            raise ConnectionError("Birden fazla ADB telefonu var; seri numarası seçilmeli")
        return ready[0]

    def _validate_video_device(self) -> None:
        path = Path(self._config.video_device)
        if not self._video_device_ready():
            raise FileNotFoundError(
                f"{path} hazır değil; v4l2loopback kurulum scriptini çalıştırın"
            )

    def _video_device_ready(self) -> bool:
        path = Path(self._config.video_device)
        try:
            return path.exists() and stat.S_ISCHR(path.stat().st_mode)
        except OSError:
            return False

    def _adb_serial_is_ready(self, serial: str) -> bool:
        try:
            result = self._run_command(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=True,
            )
        except Exception:
            return False
        devices = self.parse_adb_devices(result.stdout)
        if serial:
            return devices.get(serial) == "device"
        return any(state == "device" for state in devices.values())

    def _launch(self, serial: str) -> Any:
        return self._popen(
            self.build_command(self._config, serial),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    @staticmethod
    def build_command(config: PhoneCameraSettings, serial: str) -> list[str]:
        command = [
            "scrcpy",
            "--serial",
            serial,
            "--video-source=camera",
            f"--camera-size={config.width}x{config.height}",
            f"--camera-fps={config.fps}",
            f"--video-bit-rate={config.bitrate_mbps}M",
            f"--v4l2-sink={config.video_device}",
            "--no-playback",
            "--no-audio",
        ]
        if config.camera_id:
            command.append(f"--camera-id={config.camera_id}")
        else:
            command.append(f"--camera-facing={config.camera_facing}")
        return command

    @staticmethod
    def parse_adb_devices(output: str) -> dict[str, str]:
        devices: dict[str, str] = {}
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("List of devices"):
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                devices[parts[0]] = parts[1]
        return devices

    def _read_process_output(self, process: Any) -> None:
        stream = getattr(process, "stdout", None)
        if stream is None:
            return
        for line in stream:
            message = line.strip()
            if message:
                self._event(
                    "phone_camera.scrcpy_output",
                    message,
                    severity="debug",
                )

    @staticmethod
    def _terminate_process(process: Any | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def _publish_state(self, **changes: object) -> None:
        with self._state_lock:
            updated = replace(
                self._state,
                updated_at=datetime.now(UTC),
                **changes,
            )
            if updated == self._state:
                return
            self._state = updated
        self._changed.publish_isolated(updated)

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
