from __future__ import annotations

import threading
from collections.abc import Iterable

from src.core.events import EventBus
from src.models.commands import CommandRequest, CommandResult
from src.models.mission import MapOverlay
from src.services.contracts.commands import CommandService
from src.services.contracts.orin import OrinService
from src.services.contracts.onboard import OnboardMissionServiceContract
from src.services.contracts.parameters import ParameterService
from src.services.contracts.vehicle import VehicleService
from src.services.logging_service import ServiceLogger


class DispatchCommandService(CommandService):
    _DEFAULT_TASKS = (
        "TARGET_LOCK",
        "KAMIKAZE",
        "WAYPOINT_TRACK",
        "AUTO_TAKEOFF",
        "AUTO_LAND",
        "SURVEY_AREA",
    )
    _STANDARD_TASK_MODES = {
        "AUTO_TAKEOFF": "TAKEOFF",
        "AUTO_LAND": "AUTOLAND",
    }
    _BUILTIN_ORIN_TASKS = frozenset(
        {"TARGET_LOCK", "KAMIKAZE", "WAYPOINT_TRACK", "SURVEY_AREA"}
    )

    def __init__(
        self,
        vehicle: VehicleService,
        parameters: ParameterService,
        logger: ServiceLogger | None = None,
        orin: OrinService | None = None,
        onboard: OnboardMissionServiceContract | None = None,
        configured_orin_tasks: Iterable[str] = (),
    ) -> None:
        self._vehicle = vehicle
        self._parameters = parameters
        self._logger = logger
        self._orin = orin
        self._onboard = onboard
        configured = tuple(
            dict.fromkeys(
                str(task).strip().upper()
                for task in configured_orin_tasks
                if str(task).strip()
            )
        )
        self._orin_tasks = self._BUILTIN_ORIN_TASKS | frozenset(configured)
        self._available_tasks = self._DEFAULT_TASKS + tuple(
            task for task in configured if task not in self._DEFAULT_TASKS
        )
        self._dispatched = EventBus[CommandRequest]()
        self._completed = EventBus[CommandResult]()
        # MAVLink command transactions share one router and acknowledgements.
        # Serializing operator commands prevents concurrent waits from racing
        # for the same COMMAND_ACK or HEARTBEAT response.
        self._dispatch_lock = threading.Lock()

    @property
    def available_tasks(self) -> tuple[str, ...]:
        return self._available_tasks

    @property
    def dispatched(self) -> EventBus[CommandRequest]:
        return self._dispatched

    @property
    def completed(self) -> EventBus[CommandResult]:
        return self._completed

    def dispatch(self, request: CommandRequest) -> CommandResult:
        with self._dispatch_lock:
            return self._dispatch_serial(request)

    def _dispatch_serial(self, request: CommandRequest) -> CommandResult:
        self._dispatched.publish(request)
        request_details = self._request_log_details(request)
        try:
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
                    device_id=self._vehicle.state.device_id if self._vehicle.connected else None,
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
                    device_id=self._vehicle.state.device_id if self._vehicle.connected else None,
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
                    device_id=self._vehicle.state.device_id if self._vehicle.connected else None,
                )
        self._completed.publish(result)
        return result

    def _execute(self, request: CommandRequest) -> tuple[bool, dict[str, object]]:
        if request.code in {"ONBOARD_TASK_STOP", "ONBOARD_TASK_CANCEL"}:
            if self._onboard is None:
                raise ConnectionError("Onboard görev servisi kullanılamıyor")
            task = str(request.parameters.get("task", "kamikaze.execute"))
            receipt = (
                self._onboard.stop_task(task)
                if request.code == "ONBOARD_TASK_STOP"
                else self._onboard.cancel_task(task)
            )
            if not receipt.accepted:
                raise RuntimeError(receipt.message)
            return True, {
                "task": task,
                "target": "orin",
                "request_id": receipt.request_id,
                "orin_execution": receipt.response.get("execution", "acknowledged"),
            }
        if request.code == "APPLY_MODE":
            mode = str(request.parameters.get("mode", "")).strip().upper()
            if not mode:
                raise ValueError("Uygulanacak uçuş modu seçilmedi")
            details: dict[str, object] = {"mode": mode}
            forwarded = CommandRequest(
                "SET_MODE",
                {"mode": mode},
                request.source,
            )
            return self._vehicle.execute(forwarded), details
        if request.code in {"PARAM_APPLY", "SET_TARGET_PROFILE"}:
            changes = self._parameter_changes(request)
            self._parameters.update_values(changes)
            profile_field_count = len(changes)
            if request.code == "SET_TARGET_PROFILE" and "target_altitude" in request.parameters:
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
            if task in self._orin_tasks and self._orin is not None:
                return self._execute_orin_task(task, request)
            forwarded = CommandRequest(
                task,
                {key: value for key, value in request.parameters.items() if key != "task"},
                request.source,
            )
            return self._vehicle.execute(forwarded), {"task": task}
        if request.code in self._orin_tasks and self._orin is not None:
            return self._execute_orin_task(request.code, request)
        if request.code == "FORCE_ARM":
            confirmed = self._vehicle.execute(request)
            return confirmed, {
                "forced": True,
                "operator_confirmation": request.parameters.get("operator_confirmation") is True,
                "normal_arm_error": str(request.parameters.get("normal_arm_error", "")),
            }
        return self._vehicle.execute(request), {}

    def _execute_orin_task(
        self,
        task: str,
        request: CommandRequest,
    ) -> tuple[bool, dict[str, object]]:
        assert self._orin is not None
        if self._onboard is not None and task == "KAMIKAZE":
            receipt = self._onboard.start_task(task, source=request.source)
            if not receipt.accepted:
                raise RuntimeError(receipt.message)
            return True, {
                "task": task,
                "target": "orin",
                "request_id": receipt.request_id,
                "round_trip_ms": receipt.round_trip_ms,
                "orin_execution": receipt.response.get("execution", "acknowledged"),
                "waiting_for_mode": bool(receipt.response.get("waiting_for_mode", False)),
                "mission_id": receipt.response.get("mission_id"),
                "orin_response": receipt.response,
            }
        parameters = {
            key: value
            for key, value in request.parameters.items()
            if key != "task"
        }
        receipt = self._orin.issue(
            "task.start",
            {
                "task": task,
                "parameters": parameters,
                "source": request.source,
                "requested_at": request.timestamp.isoformat(),
            },
        )
        if not receipt.accepted:
            raise RuntimeError(receipt.message)
        return True, {
            "task": task,
            "target": "orin",
            "request_id": receipt.request_id,
            "round_trip_ms": receipt.round_trip_ms,
            "orin_execution": receipt.response.get("execution", "acknowledged"),
            "orin_response": receipt.response,
        }

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
                "uçuş cihazı tarafından kabul edildi"
            )
        if request.code == "SET_TARGET_PROFILE":
            return f"{int(details.get('profile_field_count', 0))} uçuş profili değeri doğrulandı"
        if request.code == "SET_MISSION_TASK":
            target = "Orin" if details.get("target") == "orin" else "uçuş cihazı"
            if details.get("orin_execution") == "state_only":
                return (
                    f"{details.get('task', 'Otonom görev')} Orin görev durumu olarak seçildi; "
                    "algoritma işleyicisi henüz bağlı değil"
                )
            if details.get("orin_execution") == "completed":
                return f"{details.get('task', 'Otonom görev')} Orin scripti başarıyla tamamlandı"
            return f"{details.get('task', 'Otonom görev')} {target} tarafından doğrulandı"
        if request.code == "APPLY_MODE":
            mode = str(details.get("mode", "Uçuş modu"))
            return f"{mode} modu heartbeat ile doğrulandı"
        if request.code == "FORCE_ARM":
            return "ZORLA ARM uçuş cihazı heartbeat verisiyle doğrulandı"
        return (
            f"{request.code} uçuş cihazında doğrulandı"
            if confirmed
            else f"{request.code} uçuş cihazına iletildi; bu mesaj türü onay üretmiyor"
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
                "mode": str(request.parameters.get("mode", "")).strip().upper()
                or "-",
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
                    len(raw_overlays)
                    if isinstance(raw_overlays, (list, tuple))
                    else 0
                ),
            )
        if request.code == "SET_MISSION_TASK":
            return {
                "task": str(request.parameters.get("task", "")).strip().upper()
                or "-",
            }
        if request.code in {"GOTO", "SET_HOME"}:
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
            len(overlays)
            if requested_item_count is None
            else int(requested_item_count)
        )
        return {
            "requested_item_count": total_count,
            "vehicle_item_count": len(mission_items),
            "ignored_item_count": max(0, total_count - len(mission_items)),
            "item_types": dict(sorted(type_counts.items())),
            "loop": bool(loop),
            "repeat_mode": "loop" if loop else "once",
        }
