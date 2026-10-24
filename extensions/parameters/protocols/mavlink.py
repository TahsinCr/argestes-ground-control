from __future__ import annotations

import math
import struct
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from src.core.events import Subscription
from src.extension.api.parameters import ParameterSyncState, ParameterSyncStatus
from src.models.parameters import Parameter
from src.models.vehicle import VehicleConnectionState
from src.services.logging_service import ServiceLogger
from extensions.parameters.errors import ParameterWriteError
from extensions.parameters.mutable import MutableParameterService
from extensions.mavlink.ports import MavlinkVehiclePort


@dataclass(slots=True)
class _PendingWrite:
    expected: float
    value_type: int | None
    event: threading.Event
    confirmed: bool = False


class VehicleParameterService(MutableParameterService):
    """Fetch vehicle parameters and confirm writes from vehicle feedback."""

    PROGRESS_BATCH_SIZE = 64
    PROGRESS_INTERVAL_S = 1.0

    def __init__(
        self,
        vehicle: MavlinkVehiclePort,
        logger: ServiceLogger | None = None,
        *,
        write_timeout: float = 5.0,
        refresh_on_connect: bool = True,
        max_refresh_attempts: int = 3,
        default_loader: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        super().__init__()
        if write_timeout <= 0:
            raise ValueError("Parametre yazma zaman aşımı pozitif olmalı")
        self._vehicle = vehicle
        self._logger = logger
        self._write_timeout = float(write_timeout)
        self._refresh_on_connect = refresh_on_connect
        self._max_refresh_attempts = max(1, int(max_refresh_attempts))
        self._default_loader = default_loader
        self._subscriptions: list[Subscription] = []
        self._parameter_buffer: dict[str, Parameter] = {}
        self._progress_buffer: dict[str, Parameter] = {}
        self._last_progress_publish = 0.0
        self._received_indexes: set[int] = set()
        self._expected_count = 0
        self._loading = False
        self._pending_writes: dict[str, _PendingWrite] = {}
        self._refresh_attempt = 0
        self._refresh_progress_count = 0
        self._refresh_generation = 0
        self._refresh_timer: threading.Timer | None = None
        self._defaults_thread: threading.Thread | None = None
        self._defaults_reload_pending = False
        self._defaults_generation = 0
        self._sync_lock = threading.RLock()

    def start(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions.extend(
            [
                self._vehicle.subscribe("PARAM_VALUE", self._consume_message),
                self._vehicle.connection_changed.subscribe(self._on_connection_changed),
            ]
        )
        self._changed.publish(self.current)
        if self._vehicle.connected and self._refresh_on_connect:
            self.refresh()
        elif not self._vehicle.connected:
            self._set_sync_status(
                ParameterSyncStatus(ParameterSyncState.DISCONNECTED)
            )

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        with self._sync_lock:
            self._refresh_generation += 1
            self._defaults_generation += 1
            self._defaults_reload_pending = False
            self._cancel_refresh_timer()
            for pending in self._pending_writes.values():
                pending.event.set()
            self._pending_writes.clear()
            self._parameter_buffer.clear()
            self._progress_buffer.clear()
            self._received_indexes.clear()
            self._loading = False
            self._set_sync_status(ParameterSyncStatus())

    def refresh(self) -> None:
        if not self._vehicle.connected:
            with self._sync_lock:
                self._refresh_generation += 1
                self._cancel_refresh_timer()
                self._loading = False
                self._set_sync_status(
                    ParameterSyncStatus(ParameterSyncState.DISCONNECTED)
                )
            raise ConnectionError("Parametre listesi için uçuş cihazı bağlı değil")
        with self._sync_lock:
            self._refresh_generation += 1
            generation = self._refresh_generation
            self.replace(())
            self._defaults_generation += 1
            self._cancel_refresh_timer()
            self._parameter_buffer.clear()
            self._progress_buffer.clear()
            self._received_indexes.clear()
            self._expected_count = 0
            self._loading = True
            self._refresh_attempt = 1
            self._refresh_progress_count = 0
            self._last_progress_publish = time.monotonic()
            self._set_sync_status(ParameterSyncStatus(ParameterSyncState.LOADING))
        try:
            self._vehicle.request_parameters()
        except Exception:
            with self._sync_lock:
                if generation == self._refresh_generation:
                    self._loading = False
                    received = len(self._received_indexes) or len(self._parameter_buffer)
                    expected = self._expected_count
                    self._set_sync_status(
                        ParameterSyncStatus(
                            ParameterSyncState.FAILED,
                            received,
                            expected or None,
                        )
                    )
            raise
        self._arm_refresh_timer(generation)

    def update_values(self, changes: dict[str, object]) -> None:
        if not changes:
            return
        if not self._vehicle.connected:
            error = ConnectionError("Parametre yazmak için uçuş cihazı bağlı değil")
            self._event(
                "parameter.write_failed",
                str(error),
                severity="danger",
                details={"parameter_names": sorted(changes)},
            )
            self._changed.publish(self.current)
            raise error
        parameters: dict[str, tuple[Parameter, object]] = {}
        for key, value in changes.items():
            parameter = self._find(key)
            if parameter is None:
                raise KeyError(f"Bilinmeyen parametre: {key}")
            self._numeric_value(key, value)
            parameters[key] = (parameter, value)

        with self._sync_lock:
            overlap = set(parameters) & set(self._pending_writes)
            if overlap:
                raise RuntimeError(
                    f"Parametre yazımı zaten sürüyor: {', '.join(sorted(overlap))}"
                )
            pending = {
                key: _PendingWrite(
                    self._expected_wire_value(
                        key, value, parameter.value_type
                    ),
                    parameter.value_type,
                    threading.Event(),
                )
                for key, (parameter, value) in parameters.items()
            }
            sync_state = self.sync_status.state
            if sync_state is not ParameterSyncState.COMPLETE:
                raise RuntimeError(
                    "MAVLink parametre listesi yazma öncesi eksiksiz olmalı "
                    f"(geçerli durum: {sync_state.value})"
                )
            self._pending_writes.update(pending)

        started = time.monotonic()
        attempted: list[str] = []
        failure: Exception | None = None
        try:
            for key, (parameter, value) in parameters.items():
                attempted.append(key)
                try:
                    self._vehicle.set_parameter(
                        parameter.key, value, parameter.value_type
                    )
                except Exception as exc:
                    failure = exc
                    break
            deadline = started + self._write_timeout
            for key in attempted:
                write = pending[key]
                write.event.wait(max(0.0, deadline - time.monotonic()))
            unconfirmed = [key for key in attempted if not pending[key].confirmed]
            if failure is None and unconfirmed:
                failure = TimeoutError(
                    "Araç parametre doğrulaması zaman aşımı: "
                    + ", ".join(sorted(unconfirmed))
                )
            if failure is not None:
                confirmed_values = {
                    key: parameters[key][1]
                    for key in attempted
                    if pending[key].confirmed
                }
                uncertain = tuple(
                    key for key in attempted if key not in confirmed_values
                )
                unattempted = tuple(key for key in parameters if key not in attempted)
                detail = (
                    "Bazı parametreler doğrulandı; doğrulanmayanların araçtaki durumu "
                    "belirsiz olabilir. Araç değerlerini yenileyip kontrol edin."
                )
                raise ParameterWriteError(
                    f"{failure}. {detail}",
                    confirmed_values=confirmed_values,
                    uncertain_keys=uncertain,
                    unattempted_keys=unattempted,
                ) from failure
        except Exception as exc:
            self._event(
                "parameter.write_failed",
                f"Parametre değişikliği doğrulanamadı: {exc}",
                severity="danger",
                details={
                    "parameter_names": sorted(parameters),
                    "confirmed_parameters": sorted(
                        getattr(exc, "confirmed_values", {})
                    ),
                    "uncertain_parameters": list(
                        getattr(exc, "uncertain_keys", ())
                    ),
                    "unattempted_parameters": list(
                        getattr(exc, "unattempted_keys", ())
                    ),
                },
            )
            self._changed.publish(self.current)
            raise
        finally:
            with self._sync_lock:
                for key in pending:
                    self._pending_writes.pop(key, None)
        self._event(
            "parameter.write_confirmed",
            f"{len(parameters)} parametre uçuş cihazında doğrulandı",
            severity="success",
            details={
                "changes": {
                    key: value for key, (_, value) in parameters.items()
                },
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            },
        )

    def _on_connection_changed(self, state: VehicleConnectionState) -> None:
        if state.connected and self._refresh_on_connect:
            self.refresh()
        elif not state.connected:
            with self._sync_lock:
                self._refresh_generation += 1
                self._defaults_generation += 1
                self._defaults_reload_pending = False
                self._cancel_refresh_timer()
                self._loading = False
                self._parameter_buffer.clear()
                self._progress_buffer.clear()
                self._received_indexes.clear()
                for pending in self._pending_writes.values():
                    pending.event.set()
                self._set_sync_status(
                    ParameterSyncStatus(ParameterSyncState.DISCONNECTED)
                )
                self.replace(())

    def _consume_message(self, message: Any) -> None:
        if str(message.get_type()).upper() != "PARAM_VALUE":
            return
        state = self._vehicle.state
        for getter_name, expected in (
            ("get_srcSystem", state.system_id),
            ("get_srcComponent", state.component_id),
        ):
            getter = getattr(message, getter_name, None)
            if expected is None or not callable(getter):
                continue
            try:
                if int(getter()) != int(expected):
                    return
            except Exception:
                return
        key = self._parameter_name(message.param_id)
        if not key:
            return
        raw_value = float(message.param_value)
        existing = self._find(key)
        value = self._coerce_received_value(raw_value, existing)
        raw_type = getattr(
            message, "param_type", existing.value_type if existing else None
        )
        value_type = int(raw_type) if raw_type not in {None, 0} else None
        parameter = Parameter(
            key=key,
            value=value,
            unit=existing.unit if existing else "-",
            category=existing.category if existing else self._category_for(key),
            description=existing.description
            if existing
            else "ArduPilot uçuş cihazı parametresi",
            value_type=value_type,
            default_value=existing.default_value if existing else None,
        )
        count = max(0, int(getattr(message, "param_count", 0)))
        index = int(getattr(message, "param_index", -1))

        with self._sync_lock:
            refresh_generation = self._refresh_generation
            loading = self._loading
            complete = False
            progress_parameters: tuple[Parameter, ...] = ()
            partial_status = None
            expected_count = None
            if loading:
                self._parameter_buffer[key] = parameter
                self._progress_buffer[key] = parameter
                self._expected_count = max(self._expected_count, count)
                if count > 0 and 0 <= index < count:
                    self._received_indexes.add(index)
                complete = (
                    self._expected_count > 0
                    and len(self._received_indexes) >= self._expected_count
                )
                if complete:
                    expected_count = self._expected_count
                    received_parameters = tuple(self._parameter_buffer.values())
                    self._parameter_buffer.clear()
                    self._progress_buffer.clear()
                    self._received_indexes.clear()
                    self._loading = False
                    self._cancel_refresh_timer()
                else:
                    received_parameters = ()
                    now = time.monotonic()
                    if (
                        len(self._progress_buffer) >= self.PROGRESS_BATCH_SIZE
                        or now - self._last_progress_publish >= self.PROGRESS_INTERVAL_S
                    ):
                        progress_parameters = tuple(self._progress_buffer.values())
                        self._progress_buffer.clear()
                        self._last_progress_publish = now
                        partial_status = ParameterSyncStatus(
                            ParameterSyncState.PARTIAL,
                            len(self._received_indexes) or len(self._parameter_buffer),
                            self._expected_count or None,
                        )
            else:
                received_parameters = ()
            pending = self._pending_writes.get(key)
            confirmed = (
                pending is not None
                and raw_value == pending.expected
                and (
                    pending.value_type is None
                    or value_type == pending.value_type
                )
            )
            if confirmed:
                pending.confirmed = True

        if progress_parameters:
            with self._sync_lock:
                if (
                    refresh_generation == self._refresh_generation
                    and self._loading
                ):
                    self.upsert_many(progress_parameters)
                    if partial_status is not None:
                        self._set_sync_status(partial_status)
        if complete:
            with self._sync_lock:
                completed_current_refresh = (
                    refresh_generation == self._refresh_generation
                    and not self._loading
                )
                if completed_current_refresh:
                    self.replace(received_parameters)
                    self._set_sync_status(
                        ParameterSyncStatus(
                            ParameterSyncState.COMPLETE,
                            len(received_parameters),
                            expected_count,
                        )
                    )
            if completed_current_refresh:
                self._event(
                    "parameter.sync_received",
                    f"Uçuş cihazından {len(received_parameters)} parametrenin tamamı alındı",
                    severity="success",
                    details={"parameter_count": len(received_parameters)},
                )
                self._start_default_load()
        elif not loading:
            previous = existing.value if existing is not None else None
            with self._sync_lock:
                applied_live_update = (
                    refresh_generation == self._refresh_generation
                    and not self._loading
                )
                if applied_live_update:
                    self.upsert(parameter)
            if applied_live_update and confirmed:
                self._event(
                    "parameter.value_echo",
                    f"{key} uçuş cihazı tarafından doğrulandı",
                    severity="success",
                    details={"parameter": key, "previous": previous, "value": value},
                )
            elif applied_live_update and previous != value:
                self._event(
                    "parameter.vehicle_update",
                    f"{key} uçuş cihazından güncellendi",
                    details={"parameter": key, "previous": previous, "value": value},
                )
        if confirmed and pending is not None:
            pending.event.set()

    def _start_default_load(self) -> None:
        loader = self._default_loader
        if loader is None or not self._vehicle.connected:
            return
        with self._sync_lock:
            active = self._defaults_thread
            if active is not None and active.is_alive():
                self._defaults_reload_pending = True
                return
            self._defaults_generation += 1
            generation = self._defaults_generation
            thread = threading.Thread(
                target=self._load_defaults,
                args=(generation, loader),
                name="VehicleParameterDefaults",
                daemon=True,
            )
            self._defaults_thread = thread
        thread.start()

    def _load_defaults(
        self,
        generation: int,
        loader: Callable[[], Mapping[str, object]],
    ) -> None:
        try:
            self._load_defaults_once(generation, loader)
        finally:
            with self._sync_lock:
                self._defaults_thread = None
                reload_requested = (
                    self._defaults_reload_pending
                    and bool(self._subscriptions)
                    and self._vehicle.connected
                )
                self._defaults_reload_pending = False
            if reload_requested:
                self._start_default_load()

    def _load_defaults_once(
        self,
        generation: int,
        loader: Callable[[], Mapping[str, object]],
    ) -> None:
        try:
            defaults = dict(loader())
        except Exception as exc:
            with self._sync_lock:
                current = self._defaults_generation
            if generation == current and self._vehicle.connected:
                self._event(
                    "parameter.defaults_unavailable",
                    f"Parametre varsayılanları uçuş cihazından alınamadı: {exc}",
                    severity="warning",
                )
            return

        with self._sync_lock:
            if generation != self._defaults_generation or not self._vehicle.connected:
                return
        parameters = self.current
        enriched = tuple(
            replace(parameter, default_value=defaults.get(parameter.key))
            for parameter in parameters
        )
        matched = sum(parameter.key in defaults for parameter in parameters)
        if matched:
            self.replace(enriched)
        self._event(
            "parameter.defaults_received",
            f"Uçuş cihazından {matched} parametrenin varsayılan değeri alındı",
            severity="success",
            details={"parameter_count": matched, "available_count": len(defaults)},
        )

    @staticmethod
    def _parameter_name(raw_name: object) -> str:
        if isinstance(raw_name, bytes):
            return raw_name.decode("ascii", errors="ignore").rstrip("\x00").strip()
        return str(raw_name).rstrip("\x00").strip()

    @staticmethod
    def _coerce_received_value(value: float, existing: Parameter | None) -> object:
        if existing is not None:
            if isinstance(existing.value, bool):
                return bool(round(value))
            if isinstance(existing.value, int):
                return int(round(value))
        return value

    @staticmethod
    def _numeric_value(key: str, value: object) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            try:
                numeric = float(value)
            except (OverflowError, ValueError) as exc:
                raise ValueError(
                    f"Parametre MAVLink sayısal aralığı dışında: {key}"
                ) from exc
            if math.isfinite(numeric):
                return numeric
        raise TypeError(f"Parametre sonlu sayısal olmalı: {key}")

    @staticmethod
    def _wire_float32(value: float) -> float:
        """Return the exact IEEE-754 value carried by MAVLink PARAM_VALUE."""
        try:
            return struct.unpack(">f", struct.pack(">f", value))[0]
        except (OverflowError, struct.error) as exc:
            raise ValueError("Parametre MAVLink float32 aralığının dışında") from exc

    @classmethod
    def _expected_wire_value(
        cls, key: str, value: object, value_type: int | None
    ) -> float:
        numeric = cls._numeric_value(key, value)
        wire_value = cls._wire_float32(numeric)
        # The current ArduPilot-compatible path uses C-cast encoding. Refuse
        # integer writes that float32 would silently round to another value.
        if value_type in {1, 2, 3, 4, 5, 6, 7, 8}:
            intended = (
                int(value)
                if isinstance(value, int) and not isinstance(value, bool)
                else int(numeric)
                if numeric.is_integer()
                else None
            )
            if intended is None or int(wire_value) != intended:
                raise ValueError(
                    f"{key} değeri mevcut MAVLink C-cast kodlamasında tam "
                    "olarak temsil edilemiyor"
                )
        return wire_value

    @staticmethod
    def _category_for(key: str) -> str:
        prefixes = {
            "FS_": "failsafe",
            "FENCE_": "safety",
            "ARMING_": "safety",
            "BATT_": "power",
            "SERIAL": "telemetry",
            "SR": "telemetry",
            "GPS": "navigation",
            "EK": "navigation",
            "ARSPD": "flight",
            "TECS": "flight",
            "LAND": "landing",
            "Q_": "vtol",
        }
        return next(
            (
                category
                for prefix, category in prefixes.items()
                if key.startswith(prefix)
            ),
            "vehicle",
        )

    def _arm_refresh_timer(self, generation: int) -> None:
        with self._sync_lock:
            if generation != self._refresh_generation or not self._loading:
                return
            self._cancel_refresh_timer()
            timer = threading.Timer(
                self._write_timeout,
                self._check_refresh_timeout,
                args=(generation,),
            )
            timer.name = "VehicleParameterRefreshTimeout"
            timer.daemon = True
            self._refresh_timer = timer
            timer.start()

    def _cancel_refresh_timer(self) -> None:
        timer = self._refresh_timer
        self._refresh_timer = None
        if timer is not None:
            timer.cancel()

    def _check_refresh_timeout(self, generation: int) -> None:
        with self._sync_lock:
            if generation != self._refresh_generation or not self._loading:
                return
            progress_parameters = tuple(self._progress_buffer.values())
            self._progress_buffer.clear()
            self._last_progress_publish = time.monotonic()
            received = len(self._received_indexes) or len(self._parameter_buffer)
            expected = self._expected_count
            made_progress = received > self._refresh_progress_count
            self._refresh_progress_count = received
            self._refresh_timer = None
            if made_progress and self._vehicle.connected:
                # A complete ArduPilot catalogue can take considerably longer
                # than the write timeout over a radio. Progress means the
                # transfer is healthy; only retry after a full quiet interval.
                retry = False
                keep_waiting = True
            elif (
                self._refresh_attempt >= self._max_refresh_attempts
                or not self._vehicle.connected
            ):
                self._loading = False
                self._parameter_buffer.clear()
                self._progress_buffer.clear()
                self._received_indexes.clear()
                retry = False
                keep_waiting = False
            else:
                self._refresh_attempt += 1
                attempt = self._refresh_attempt
                retry = True
                keep_waiting = False
        if progress_parameters:
            with self._sync_lock:
                if generation != self._refresh_generation:
                    return
                self.upsert_many(progress_parameters)
        if keep_waiting:
            with self._sync_lock:
                if generation != self._refresh_generation or not self._loading:
                    return
                self._set_sync_status(
                    ParameterSyncStatus(
                        ParameterSyncState.PARTIAL if received else ParameterSyncState.LOADING,
                        received,
                        expected or None,
                    )
                )
                self._arm_refresh_timer(generation)
            return
        if not retry:
            with self._sync_lock:
                if generation != self._refresh_generation:
                    return
                self._set_sync_status(
                    ParameterSyncStatus(
                        ParameterSyncState.FAILED,
                        received,
                        expected or None,
                    )
                )
            self._event(
                "parameter.sync_failed",
                "Araç parametre listesi eksiksiz alınamadı",
                severity="danger",
                details={"received_count": received, "expected_count": expected},
            )
            return
        with self._sync_lock:
            if generation != self._refresh_generation or not self._loading:
                return
            self._set_sync_status(
                ParameterSyncStatus(
                    ParameterSyncState.PARTIAL if received else ParameterSyncState.LOADING,
                    received,
                    expected or None,
                )
            )
        self._event(
            "parameter.sync_retry",
            f"Eksik parametre listesi yeniden isteniyor ({attempt}/{self._max_refresh_attempts})",
            severity="warning",
            details={"received_count": received, "expected_count": expected},
        )
        try:
            with self._sync_lock:
                if generation != self._refresh_generation or not self._loading:
                    return
                self._vehicle.request_parameters()
        except Exception as exc:
            self._event(
                "parameter.sync_retry_failed",
                f"Parametre listesi yeniden istenemedi: {exc}",
                severity="danger",
            )
        self._arm_refresh_timer(generation)

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
                details=details,
                device_id=self._vehicle.state.device_id
                if self._vehicle.connected
                else None,
            )
