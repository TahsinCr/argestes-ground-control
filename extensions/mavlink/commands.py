from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from types import MappingProxyType
from src.core.events import EventBus
from src.models.commands import CommandDefinition, CommandRequest, CommandResult
from src.models.mission import MapOverlay
from src.models.vehicle import VehicleCapabilities
from src.services.abstracts.commands import CommandService
from src.extension.api.parameters import ParameterService
from src.services.abstracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


class DispatchCommandService(CommandService):
    _DEFINITIONS = (
        CommandDefinition("ARM", "safety"),
        CommandDefinition("DISARM", "safety"),
        CommandDefinition("FORCE_ARM", "safety", requires_confirmation=True),
        CommandDefinition("SET_MODE", "vehicle", ("mode",)),
        CommandDefinition("GOTO", "navigation", ("latitude", "longitude"), True),
        CommandDefinition("SET_HOME", "navigation", ("latitude", "longitude")),
        CommandDefinition("TAKEOFF", "navigation"),
        CommandDefinition("LAND", "navigation"),
        CommandDefinition("RTL", "navigation"),
        CommandDefinition("HOLD", "navigation"),
        CommandDefinition("MISSION_START", "mission"),
        CommandDefinition("MISSION_PAUSE", "mission"),
        CommandDefinition("MISSION_RESUME", "mission"),
        CommandDefinition("MISSION_UPLOAD", "mission", ("overlays",)),
    )
    _DEFINITION_BY_CODE: Mapping[str, CommandDefinition] = MappingProxyType(
        {definition.code: definition for definition in _DEFINITIONS}
    )
    _ALIASES: Mapping[str, str] = MappingProxyType(
        {
            "RETURN_HOME": "RTL",
            "RETURN_TO_LAUNCH": "RTL",
            "LOITER": "HOLD",
            "PAUSE_MISSION": "MISSION_PAUSE",
            "RESUME_MISSION": "MISSION_RESUME",
        }
    )
    _NAVIGATION_COMMANDS = frozenset(
        definition.code
        for definition in _DEFINITIONS
        if definition.supersedes_navigation
    )
    _DEFAULT_TASKS: tuple[str, ...] = ()
    _STANDARD_TASK_MODES = {
        "AUTO_TAKEOFF": "TAKEOFF",
        "AUTO_LAND": "AUTOLAND",
    }

    def __init__(
        self,
        vehicle: VehicleService,
        parameters: ParameterService,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._vehicle = vehicle
        self._parameters = parameters
        self._logger = logger
        self._available_tasks = self._DEFAULT_TASKS
        self._dispatched = EventBus[CommandRequest]()
        self._completed = EventBus[CommandResult]()
        # MAVLink command transactions share one router and acknowledgements.
        # Serializing operator commands prevents concurrent waits from racing
        # for the same COMMAND_ACK or HEARTBEAT response.
        self._dispatch_lock = threading.Lock()
        self._navigation_lock = threading.Lock()
        self._navigation_revision = 0

    @property
    def available_tasks(self) -> tuple[str, ...]:
        return self._available_tasks

    @property
    def command_definitions(self) -> tuple[CommandDefinition, ...]:
        return self._DEFINITIONS

    @property
    def available_commands(self) -> tuple[CommandDefinition, ...]:
        capabilities = self._vehicle_capabilities()
        if capabilities is None or not capabilities.command_codes:
            return self.command_definitions
        return tuple(
            definition
            for definition in self.command_definitions
            if capabilities.supports(definition.code)
        )

    @property
    def available_vehicle_modes(self) -> tuple[str, ...]:
        capabilities = self._vehicle_capabilities()
        return capabilities.vehicle_modes if capabilities is not None else ()

    @property
    def available_command_codes(self) -> tuple[str, ...]:
        return tuple(definition.code for definition in self.available_commands)

    @property
    def dispatched(self) -> EventBus[CommandRequest]:
        return self._dispatched

    @property
    def completed(self) -> EventBus[CommandResult]:
        return self._completed

    def dispatch(self, request: CommandRequest) -> CommandResult:
        request = self._normalize_request(request)
        if self._request_expired(request):
            return self._complete_expired(request)
        navigation_revision: int | None = None
        if request.code in self._NAVIGATION_COMMANDS:
            with self._navigation_lock:
                self._navigation_revision += 1
                navigation_revision = self._navigation_revision
        with self._dispatch_lock:
            if self._request_expired(request):
                return self._complete_expired(request)
            if navigation_revision is not None:
                with self._navigation_lock:
                    latest_revision = self._navigation_revision
                if navigation_revision != latest_revision:
                    return self._complete_superseded_navigation(
                        request,
                        navigation_revision,
                        latest_revision,
                    )
            return self._dispatch_serial(request)

    @staticmethod
    def _request_expired(request: CommandRequest) -> bool:
        return (
            request.deadline_monotonic is not None
            and time.monotonic() >= request.deadline_monotonic
        )

    def _complete_expired(self, request: CommandRequest) -> CommandResult:
        message = "Komut zaman sınırını aştı; araç denetleyicisine gönderilmedi"
        result = CommandResult(
            request,
            False,
            message,
            {
                "code": request.code,
                "source": request.source,
                "error": message,
                "error_type": "TimeoutError",
            },
        )
        if self._logger:
            self._logger.event(
                "command.expired",
                message,
                severity="warning",
                details=result.details,
                device_id=(
                    self._vehicle.state.device_id if self._vehicle.connected else None
                ),
            )
        self._completed.publish(result)
        return result

    def _complete_superseded_navigation(
        self,
        request: CommandRequest,
        revision: int,
        latest_revision: int,
    ) -> CommandResult:
        result = CommandResult(
            request,
            False,
            f"{request.code} daha yeni bir hedef tarafından geçersiz kılındı",
            {
                "navigation": "superseded",
                "confirmed": False,
                "revision": revision,
                "latest_revision": latest_revision,
            },
        )
        if self._logger:
            self._logger.event(
                "command.superseded",
                result.message,
                severity="info",
                details={
                    "code": request.code,
                    "source": request.source,
                    **result.details,
                },
                device_id=(
                    self._vehicle.state.device_id if self._vehicle.connected else None
                ),
            )
        self._completed.publish(result)
        return result

    def _dispatch_serial(self, request: CommandRequest) -> CommandResult:
        self._dispatched.publish(request)
        request_details = self._request_log_details(request)
        if self._logger:
            self._logger.event(
                "command.dispatched",
                f"Komut servis katmanına aktarıldı: {request.code}",
                severity="info",
                details={
                    "code": request.code,
                    "source": request.source,
                    **request_details,
                },
                device_id=(
                    self._vehicle.state.device_id if self._vehicle.connected else None
                ),
            )
        try:
            self._validate_request(request)
            if self._request_expired(request):
                return self._complete_expired(request)
            confirmed, details = self._execute(request)
            details = {**request_details, **details}
        except NotImplementedError as exc:
            # UI catalogue entries without backend behavior are not system actions.
            result = CommandResult(
                request,
                False,
                str(exc),
                {
                    **request_details,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            if self._logger:
                self._logger.event(
                    "command.rejected",
                    f"{request.code} uygulanamadı: {exc}",
                    severity="warning",
                    details={
                        "code": request.code,
                        "source": request.source,
                        **result.details,
                    },
                    device_id=self._vehicle.state.device_id
                    if self._vehicle.connected
                    else None,
                )
        except Exception as exc:
            result = CommandResult(
                request,
                False,
                str(exc),
                {
                    **request_details,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            if self._logger:
                self._logger.event(
                    "command.failed",
                    f"{request.code} işlemi başarısız: {exc}",
                    severity="danger",
                    details={
                        "code": request.code,
                        "source": request.source,
                        **result.details,
                    },
                    device_id=self._vehicle.state.device_id
                    if self._vehicle.connected
                    else None,
                )
        else:
            message = self._success_message(request, confirmed, details)
            result = CommandResult(request, True, message, details)
            if self._logger:
                self._logger.event(
                    "command.confirmed" if confirmed else "command.sent",
                    result.message,
                    severity="success" if confirmed else "info",
                    details={"code": request.code, "source": request.source, **details},
                    device_id=self._vehicle.state.device_id
                    if self._vehicle.connected
                    else None,
                )
        self._completed.publish(result)
        return result

    def _execute(self, request: CommandRequest) -> tuple[bool, dict[str, object]]:
        if request.code == "GOTO":
            return self._vehicle.execute(request), {
                "navigation": "goto",
                "latitude": float(request.parameters["latitude"]),
                "longitude": float(request.parameters["longitude"]),
            }
        if request.code == "APPLY_MODE":
            mode = str(request.parameters.get("mode", "")).strip().upper()
            if not mode:
                raise ValueError("Uygulanacak araç modu seçilmedi")
            details: dict[str, object] = {"mode": mode}
            forwarded = CommandRequest(
                "SET_MODE",
                {"mode": mode},
                request.source,
            )
            return self._vehicle.execute(forwarded), details
        if request.code in {"PARAM_APPLY", "SET_TARGET_PROFILE"}:
            changes = self._parameter_changes(request)
            service = (
                self._parameters() if callable(self._parameters) else self._parameters
            )
            if service is None:
                raise RuntimeError("Parameter extension is inactive")
            service.update_values(changes)
            profile_field_count = len(changes)
            if (
                request.code == "SET_TARGET_PROFILE"
                and "target_altitude" in request.parameters
            ):
                self._vehicle.set_default_mission_altitude(
                    float(request.parameters["target_altitude"])
                )
                profile_field_count += 1
            return True, {
                "parameter_count": len(changes),
                "profile_field_count": profile_field_count,
            }
        if request.code == "MISSION_UPLOAD":
            overlays = [
                item if isinstance(item, MapOverlay) else MapOverlay.from_dict(item)
                for item in request.parameters.get("overlays", [])
            ]
            mission_items = [item for item in overlays if item.is_vehicle_mission]
            if not mission_items:
                raise ValueError("Ucus cihazina gonderilecek görev noktasi yok")
            loop = bool(request.parameters.get("loop", False))
            item_count = self._vehicle.upload_mission(mission_items, loop=loop)
            return True, {
                **self._mission_log_details(overlays, loop=loop),
                "item_count": item_count,
            }
        if request.code == "SET_MISSION_TASK":
            task = str(request.parameters.get("task", "")).strip().upper()
            if not task:
                raise ValueError("Otonom görev seçilmedi")
            if task in self._STANDARD_TASK_MODES:
                mode = self._STANDARD_TASK_MODES[task]
                forwarded = CommandRequest(
                    "SET_MODE",
                    {"mode": mode},
                    request.source,
                )
                return self._vehicle.execute(forwarded), {"task": task, "mode": mode}
            forwarded = CommandRequest(
                task,
                {
                    key: value
                    for key, value in request.parameters.items()
                    if key != "task"
                },
                request.source,
            )
            return self._vehicle.execute(forwarded), {"task": task}
        if request.code == "FORCE_ARM":
            confirmed = self._vehicle.execute(request)
            return confirmed, {
                "forced": True,
                "operator_confirmation": request.parameters.get("operator_confirmation")
                is True,
                "normal_arm_error": str(request.parameters.get("normal_arm_error", "")),
            }
        return self._vehicle.execute(request), {
            "operation": request.code.lower(),
            "vehicle_type": self._vehicle.state.vehicle_type,
            "autopilot_type": self._vehicle.state.autopilot_type,
        }

    def _normalize_request(self, request: CommandRequest) -> CommandRequest:
        canonical_code = self._ALIASES.get(request.code, request.code)
        if canonical_code == request.code:
            return request
        return CommandRequest(
            canonical_code,
            request.parameters,
            request.source,
            request.timestamp,
            request.deadline_monotonic,
        )

    def _vehicle_capabilities(self) -> VehicleCapabilities | None:
        capabilities = getattr(self._vehicle, "capabilities", None)
        return capabilities if isinstance(capabilities, VehicleCapabilities) else None

    def _validate_request(self, request: CommandRequest) -> None:
        """Validate generic command shape before touching a vehicle adapter."""

        definition = self._DEFINITION_BY_CODE.get(request.code)
        if definition is not None:
            missing = [
                name
                for name in definition.required_parameters
                if name not in request.parameters
            ]
            if missing:
                raise ValueError(
                    f"{request.code} için zorunlu alanlar eksik: {', '.join(missing)}"
                )
            if (
                definition.requires_confirmation
                and request.parameters.get("operator_confirmation") is not True
            ):
                raise PermissionError(
                    f"{request.code} için açık operatör onayı gerekli"
                )

        capabilities = self._vehicle_capabilities()
        if (
            definition is not None
            and capabilities is not None
            and capabilities.command_codes
            and not capabilities.supports(request.code)
        ):
            raise NotImplementedError(
                f"Bağlı araç {request.code} komutunu desteklediğini bildirmiyor"
            )
        if request.code == "APPLY_MODE":
            mode = str(request.parameters.get("mode", "")).strip().upper()
            if (
                capabilities is not None
                and capabilities.vehicle_modes
                and mode not in capabilities.vehicle_modes
            ):
                raise ValueError(f"Bağlı araç modu desteklemiyor: {mode}")

    @staticmethod
    def _success_message(
        request: CommandRequest,
        confirmed: bool,
        details: dict[str, object],
    ) -> str:
        if request.code == "MISSION_UPLOAD":
            mode = "döngü görevi" if details.get("loop") else "tek seferlik görev"
            return (
                f"{int(details.get('item_count', 0))} görev öğesi {mode} olarak "
                "araç denetleyicisi tarafından kabul edildi"
            )
        if request.code == "SET_TARGET_PROFILE":
            return f"{int(details.get('profile_field_count', 0))} uçuş profili değeri doğrulandı"
        if request.code == "SET_MISSION_TASK":
            return f"{details.get('task', 'Görev')} araç denetleyicisi tarafından doğrulandı"
        if request.code == "APPLY_MODE":
            mode = str(details.get("mode", "Araç modu"))
            return f"{mode} modu heartbeat ile doğrulandı"
        if request.code == "FORCE_ARM":
            return "ZORLA ARM araç denetleyicisi heartbeat verisiyle doğrulandı"
        return (
            f"{request.code} araç denetleyicisinde doğrulandı"
            if confirmed
            else f"{request.code} araç denetleyicisine iletildi; bu mesaj türü onay üretmiyor"
        )

    @staticmethod
    def _parameter_changes(request: CommandRequest) -> dict[str, object]:
        if request.code == "PARAM_APPLY":
            return dict(request.parameters)
        changes: dict[str, object] = {}
        if "air_speed" in request.parameters:
            changes["AIRSPEED_CRUISE"] = request.parameters["air_speed"]
        if "loiter_radius" in request.parameters:
            changes["WP_LOITER_RAD"] = request.parameters["loiter_radius"]
        if not changes:
            raise ValueError("Uygulanacak araç profili parametresi bulunamadı")
        return changes

    @classmethod
    def _request_log_details(
        cls,
        request: CommandRequest,
    ) -> dict[str, object]:
        if request.code == "APPLY_MODE":
            return {
                "mode": str(request.parameters.get("mode", "")).strip().upper() or "-",
            }
        if request.code == "MISSION_UPLOAD":
            raw_overlays = request.parameters.get("overlays", [])
            overlays: list[MapOverlay] = []
            if isinstance(raw_overlays, (list, tuple)):
                for raw_overlay in raw_overlays:
                    try:
                        overlays.append(
                            raw_overlay
                            if isinstance(raw_overlay, MapOverlay)
                            else MapOverlay.from_dict(raw_overlay)
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
            return cls._mission_log_details(
                overlays,
                loop=bool(request.parameters.get("loop", False)),
                requested_item_count=(
                    len(raw_overlays) if isinstance(raw_overlays, (list, tuple)) else 0
                ),
            )
        if request.code == "SET_MISSION_TASK":
            return {
                "task": str(request.parameters.get("task", "")).strip().upper() or "-",
            }
        if request.code in {"GOTO", "SET_HOME", "TAKEOFF", "LAND"}:
            return {
                key: request.parameters[key]
                for key in ("latitude", "longitude", "altitude")
                if key in request.parameters
            }
        if request.code == "PARAM_APPLY":
            return {"parameter_count": len(request.parameters)}
        return {}

    @staticmethod
    def _mission_log_details(
        overlays: list[MapOverlay],
        *,
        loop: bool,
        requested_item_count: int | None = None,
    ) -> dict[str, object]:
        mission_items = [item for item in overlays if item.is_vehicle_mission]
        type_counts: dict[str, int] = {}
        for item in mission_items:
            type_counts[item.overlay_type] = type_counts.get(item.overlay_type, 0) + 1
        total_count = (
            len(overlays) if requested_item_count is None else int(requested_item_count)
        )
        return {
            "requested_item_count": total_count,
            "vehicle_item_count": len(mission_items),
            "ignored_item_count": max(0, total_count - len(mission_items)),
            "item_types": dict(sorted(type_counts.items())),
            "loop": bool(loop),
            "repeat_mode": "loop" if loop else "once",
        }
