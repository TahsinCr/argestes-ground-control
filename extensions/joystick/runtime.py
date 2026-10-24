from __future__ import annotations

import array
import glob
import math
import os
import struct
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux-only device API
    fcntl = None  # type: ignore[assignment]

from src.core.events import EventBus, Subscription
from src.extension.api.vehicle import RcControl
from src.models.commands import CommandRequest
from extensions.joystick.models import (
    JoystickButtonConfig,
    JoystickChannelConfig,
    JoystickDevice,
    JoystickSettings,
    JoystickState,
)
from extensions.joystick.contracts import JoystickService
from src.services.abstracts.settings import SettingsRepository
from src.services.abstracts.vehicle import VehicleService
from src.services.abstracts.commands import CommandService
from src.services.logging_service import ServiceLogger


_JS_EVENT_BUTTON = 0x01
_JS_EVENT_AXIS = 0x02
_JS_EVENT_INIT = 0x80
_JSIOCGAXES = 0x80016A11
_JSIOCGBUTTONS = 0x80016A12
_JSIOCGNAME_128 = 0x80806A13
_JS_EVENT = struct.Struct("<IhBB")


class LinuxRcJoystickService(JoystickService):
    """Translate Linux joystick input into bounded RC channel overrides."""

    def __init__(
        self,
        settings: SettingsRepository,
        vehicle: VehicleService,
        rc_control: RcControl,
        commands: CommandService,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._settings = settings
        self._vehicle = vehicle
        self._rc_control = rc_control
        self._commands = commands
        self._logger = logger
        self._changed = EventBus[JoystickState]()
        self._state = JoystickState()
        self._state_lock = threading.RLock()
        self._action_lock = threading.Lock()
        self._action_executor: ThreadPoolExecutor | None = None
        self._action_future: Future | None = None
        self._action_generation = 0
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._settings_subscription: Subscription | None = None
        self._extension_subscription: Subscription | None = None
        self._configuration_revision = 0
        self._configuration = JoystickSettings.from_config(
            self._settings.extension_config("joystick")
        )
        self._manual_disconnect = False

    @property
    def changed(self) -> EventBus[JoystickState]:
        return self._changed

    @property
    def state(self) -> JoystickState:
        with self._state_lock:
            return replace(self._state)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._manual_disconnect = False
        with self._state_lock:
            self._action_generation += 1
            self._action_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="JoystickButtonAction"
            )
        self._configuration = JoystickSettings.from_config(
            self._settings.extension_config("joystick")
        )
        self._settings_subscription = self._settings.changed.subscribe(
            self._settings_changed
        )
        self._extension_subscription = self._settings.extensions_changed.subscribe(
            lambda identifier: (
                self._settings_changed(self._settings.current)
                if identifier == "joystick"
                else None
            )
        )
        self._thread = threading.Thread(
            target=self._run, name="RcJoystick", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._cancel_button_actions(wait=True, shutdown=True)
        if self._settings_subscription is not None:
            self._settings_subscription.cancel()
            self._settings_subscription = None
        if self._extension_subscription is not None:
            self._extension_subscription.cancel()
            self._extension_subscription = None
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self._release_override()
        self._publish(JoystickState())

    def reconnect(self) -> None:
        self._manual_disconnect = False
        with self._state_lock:
            self._configuration_revision += 1
        self._wake_event.set()

    def disconnect(self) -> None:
        self._manual_disconnect = True
        with self._state_lock:
            self._configuration_revision += 1
        self._cancel_button_actions(wait=False)
        self._wake_event.set()
        self._release_override()
        self._publish(JoystickState())

    def devices(self) -> tuple[JoystickDevice, ...]:
        found: list[JoystickDevice] = []
        for path in sorted(glob.glob("/dev/input/js*")):
            try:
                found.append(self._describe(path))
            except OSError:
                continue
        return tuple(found)

    def _settings_changed(self, settings) -> None:
        configuration = JoystickSettings.from_config(
            settings.extensions.get("joystick", {}).get("config", {})
        )
        if configuration == self._configuration:
            return
        self._configuration = configuration
        self._manual_disconnect = False
        with self._state_lock:
            self._configuration_revision += 1
        self._cancel_button_actions(wait=False)
        self._wake_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            config = JoystickSettings.from_config(
                self._settings.extension_config("joystick")
            )
            if not config.enabled or self._manual_disconnect:
                self._wake_event.wait(0.5)
                self._wake_event.clear()
                continue
            path = config.device_path
            if not path:
                self._publish(JoystickState(error="Joystick seçilmedi"))
                self._wake_event.wait(0.5)
                self._wake_event.clear()
                continue
            with self._state_lock:
                revision = self._configuration_revision
            try:
                self._device_session(path, config, revision)
            except Exception as exc:
                self._release_override()
                self._publish(JoystickState(device_path=path, error=str(exc)))
                self._event(
                    "joystick.worker_failed",
                    f"Joystick oturumu sonlandı ve yeniden denenecek: {exc}",
                    "warning" if isinstance(exc, (OSError, ValueError)) else "danger",
                    {"device": path, "error_type": exc.__class__.__name__},
                )
                self._wake_event.wait(1.0)
                self._wake_event.clear()

    def _device_session(
        self,
        path: str,
        config: JoystickSettings,
        revision: int,
    ) -> None:
        with self._state_lock:
            self._action_generation += 1
            action_generation = self._action_generation
        device = self._describe(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        axes = [0.0] * device.axis_count
        buttons = [False] * device.button_count
        previous_buttons = [False] * device.button_count
        toggles: dict[int, bool] = {}
        send_interval = 1.0 / config.send_rate_hz
        next_send = time.monotonic()
        self._event(
            "joystick.connected",
            f"Joystick bağlandı: {device.name}",
            "success",
            {"device": path, "axes": device.axis_count, "buttons": device.button_count},
        )
        try:
            while not self._stop_event.is_set() and not self._manual_disconnect:
                with self._state_lock:
                    if revision != self._configuration_revision:
                        break
                changed = self._read_events(descriptor, axes, buttons)
                if changed:
                    for index, pressed in enumerate(buttons):
                        if pressed and not previous_buttons[index]:
                            toggles[index] = not toggles.get(index, False)
                            self._execute_button_action(config, index, action_generation)
                    previous_buttons[:] = buttons
                pwm = self.channel_pwm(config, axes, buttons, toggles)
                now = time.monotonic()
                transmitting = self._vehicle.connected
                if (
                    changed
                    or not self.state.connected
                    or self.state.transmitting != transmitting
                ):
                    self._publish(
                        JoystickState(
                            connected=True,
                            transmitting=transmitting,
                            device_path=path,
                            device_name=device.name,
                            axes=tuple(axes),
                            buttons=tuple(buttons),
                            channel_pwm=pwm,
                        )
                    )
                if now >= next_send:
                    next_send = now + send_interval
                    if transmitting:
                        self._rc_control.send_rc_override(pwm)
                self._wake_event.wait(min(0.01, max(0.001, next_send - now)))
                self._wake_event.clear()
        finally:
            os.close(descriptor)
            # Do not hold RC cleanup while an already dispatched command waits
            # for its protocol timeout. The generation check rejects stale
            # work and the single-flight lock remains held until completion.
            self._cancel_button_actions(wait=False)
            self._release_override()
            self._publish(JoystickState())

    def _execute_button_action(
        self, config: JoystickSettings, button: int, generation: int
    ) -> None:
        assignment = next(
            (
                item
                for item in config.buttons
                if item.button >= 0 and item.button == button
            ),
            None,
        )
        if (
            assignment is None
            or not assignment.action
            or not self._vehicle.connected
            or self._stop_event.is_set()
        ):
            return
        with self._state_lock:
            executor = self._action_executor
            if generation != self._action_generation or executor is None:
                return
        if not self._action_lock.acquire(blocking=False):
            self._event(
                "joystick.button_action_busy",
                "Önceki joystick buton aksiyonu tamamlanmadan yeni aksiyon atlandı",
                "warning",
                {"button": button + 1, "action": assignment.action},
            )
            return
        try:
            future = executor.submit(self._run_button_action, assignment, generation)
            self._action_future = future
            future.add_done_callback(self._button_action_finished)
        except RuntimeError as exc:
            self._action_lock.release()
            self._event(
                "joystick.button_action_failed",
                "Joystick buton aksiyonu oturum kapanırken başlatılamadı",
                "warning",
                {"button": button + 1, "action": assignment.action},
            )

    def _run_button_action(
        self, assignment: JoystickButtonConfig, generation: int
    ) -> None:
        parameters: dict[str, object] = {}
        if assignment.action == "SET_MODE":
            parameters["mode"] = assignment.mode
        elif assignment.action == "SET_SERVO":
            parameters.update(servo=assignment.servo, pwm=assignment.pwm)
        try:
            with self._state_lock:
                current = generation == self._action_generation
            if not current or self._stop_event.is_set() or not self._vehicle.connected:
                return
            result = self._commands.dispatch(
                CommandRequest(assignment.action, parameters, source="joystick")
            )
            accepted = result.accepted
            self._event(
                "joystick.button_action",
                result.message
                or f"Joystick buton aksiyonu {'gönderildi' if accepted else 'reddedildi'}: {assignment.action}",
                "success" if accepted else "warning",
                {
                    "button": assignment.button + 1,
                    "action": assignment.action,
                    "confirmed": bool(result.details.get("confirmed", False)),
                    **parameters,
                },
            )
        except Exception as exc:
            self._event(
                "joystick.button_action_failed",
                f"Joystick buton aksiyonu başarısız: {exc}",
                "danger",
                {"button": assignment.button + 1, "action": assignment.action},
            )
    def _button_action_finished(self, future: Future) -> None:
        try:
            error = future.exception()
            if error is not None:
                self._event(
                    "joystick.button_action_failed",
                    "Joystick buton aksiyonu beklenmedik biçimde başarısız oldu",
                    "danger",
                )
        except Exception:
            pass
        finally:
            self._action_lock.release()

    def _cancel_button_actions(self, *, wait: bool, shutdown: bool = False) -> None:
        with self._state_lock:
            self._action_generation += 1
            future = self._action_future
            executor = self._action_executor
            if shutdown:
                self._action_executor = None
        if future is not None:
            future.cancel()
        if shutdown and executor is not None:
            # Never block plugin/UI shutdown on an already-dispatched vehicle
            # operation. Queued actions are cancelled; an in-flight operation
            # is reported as pending because Python cannot safely interrupt it.
            if future is not None and not future.done():
                self._event(
                    "joystick.button_action_shutdown_pending",
                    "Joystick kapanırken gönderilmiş buton komutu hâlâ sonuçlanmadı",
                    "warning",
                    {},
                )
            executor.shutdown(wait=False, cancel_futures=True)
        elif wait and future is not None:
            try:
                future.result()
            except Exception:
                pass
            # A Future may notify waiters just before invoking its callbacks;
            # wait for the completion callback to release the single-flight
            # guard before a replacement device session accepts actions.
            self._action_lock.acquire()
            self._action_lock.release()

    @classmethod
    def channel_pwm(
        cls,
        config: JoystickSettings,
        axes: list[float] | tuple[float, ...],
        buttons: list[bool] | tuple[bool, ...],
        toggles: dict[int, bool] | None = None,
    ) -> tuple[int | None, ...]:
        toggle_state = toggles or {}
        output: list[int | None] = [None] * 16
        for channel in config.channels:
            normalized: float | None = None
            if channel.control.startswith("axis:"):
                index = cls._control_index(channel.control)
                if index < len(axes):
                    normalized = cls._shape_axis(float(axes[index]), channel)
            elif channel.control.startswith("button:"):
                index = cls._control_index(channel.control)
                if index < len(buttons):
                    normalized = 1.0 if buttons[index] else -1.0
            elif channel.control.startswith("toggle:"):
                index = cls._control_index(channel.control)
                normalized = 1.0 if toggle_state.get(index, False) else -1.0
            if normalized is not None:
                if channel.reversed:
                    normalized = -normalized
                output[channel.channel - 1] = cls._to_pwm(normalized, channel)
        return tuple(output)

    @staticmethod
    def _shape_axis(value: float, config: JoystickChannelConfig) -> float:
        value = max(-1.0, min(1.0, value))
        magnitude = abs(value)
        if magnitude <= config.deadzone:
            return 0.0
        magnitude = (magnitude - config.deadzone) / (1.0 - config.deadzone)
        expo = config.expo / 100.0
        magnitude = (1.0 - expo) * magnitude + expo * magnitude**3
        return math.copysign(magnitude, value)

    @staticmethod
    def _to_pwm(value: float, config: JoystickChannelConfig) -> int:
        value = max(-1.0, min(1.0, value))
        if value >= 0.0:
            return round(config.pwm_trim + value * (config.pwm_max - config.pwm_trim))
        return round(config.pwm_trim + value * (config.pwm_trim - config.pwm_min))

    @staticmethod
    def _control_index(control: str) -> int:
        try:
            return max(0, int(control.split(":", 1)[1]))
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Geçersiz joystick denetimi: {control}") from exc

    @staticmethod
    def _read_events(descriptor: int, axes: list[float], buttons: list[bool]) -> bool:
        changed = False
        while True:
            try:
                payload = os.read(descriptor, _JS_EVENT.size)
            except BlockingIOError:
                break
            if len(payload) != _JS_EVENT.size:
                raise OSError("Joystick bağlantısı kesildi")
            _timestamp, value, event_type, number = _JS_EVENT.unpack(payload)
            kind = event_type & ~_JS_EVENT_INIT
            if kind == _JS_EVENT_AXIS and number < len(axes):
                axes[number] = max(-1.0, min(1.0, value / 32767.0))
                changed = True
            elif kind == _JS_EVENT_BUTTON and number < len(buttons):
                buttons[number] = bool(value)
                changed = True
        return changed

    @staticmethod
    def _describe(path: str) -> JoystickDevice:
        if fcntl is None:
            raise OSError("Joystick cihazları bu platformda desteklenmiyor")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            axes = array.array("B", [0])
            buttons = array.array("B", [0])
            name = array.array("B", [0] * 128)
            fcntl.ioctl(descriptor, _JSIOCGAXES, axes, True)
            fcntl.ioctl(descriptor, _JSIOCGBUTTONS, buttons, True)
            fcntl.ioctl(descriptor, _JSIOCGNAME_128, name, True)
            decoded = name.tobytes().split(b"\0", 1)[0].decode("utf-8", "replace")
            return JoystickDevice(path, decoded or Path(path).name, axes[0], buttons[0])
        finally:
            os.close(descriptor)

    def _release_override(self) -> None:
        try:
            self._rc_control.release_rc_override()
        except (ConnectionError, OSError):
            pass

    def _publish(self, state: JoystickState) -> None:
        with self._state_lock:
            if state == self._state:
                return
            self._state = state
        self._changed.publish(state)

    def _event(
        self,
        action: str,
        message: str,
        severity: str,
        details: dict[str, object],
    ) -> None:
        if self._logger is not None:
            self._logger.event(action, message, severity=severity, details=details)


__all__ = ["LinuxRcJoystickService"]
