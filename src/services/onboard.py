from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from src.communication import (
    ChainStart,
    CompetitionClockUpdate,
    CompetitionEventTime,
    CompetitionServerTime as ContractCompetitionServerTime,
    ContractModel,
    HssUpdate as ContractHssUpdate,
    HssRoutePlan,
    HssTransitRequest,
    HssZone as ContractHssZone,
    HssWaypoint,
    KamikazeApproachPlanUpdate,
    KamikazeQrReport,
    KamikazeServerTime,
    KamikazeTargetUpdate,
    MissionEventsQuery,
    MissionList,
    MissionQuery,
    MissionResult,
    MissionTransition,
    PacketType,
    RecordingControl,
    TaskCancel,
    TaskPause,
    TaskResume,
    TaskStart,
    TaskStop,
    TargetLockReport,
    TargetLockStatus,
    canonical_task as contract_canonical_task,
)
from src.core.events import EventBus, Subscription
from src.models.competition import HssZone, KamikazeEvent, LockEvent, QrCoordinate
from src.models.geometry import Point
from src.models.kamikaze_approach import KamikazeApproachPlan
from src.models.onboard import (
    ACTUATOR_TEST_EVENTS,
    ACTUATOR_TEST_TASK,
    KAMIKAZE_CHECK_TASK,
    CRITICAL_KAMIKAZE_EVENTS,
    QR_EVENT_TYPES,
    TERMINAL_MISSION_PHASES,
    OnboardMissionEvent,
    OnboardMissionState,
)
from src.models.orin import (
    OrinCommandReceipt,
    OrinConnectionState,
    OrinHandlerResponse,
    OrinMessage,
)
from src.models.telemetry import TargetTrack
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.onboard import OnboardMissionServiceContract
from src.services.abstracts.orin import OrinService
from src.services.abstracts.telemetry import TelemetryService
from src.services.async_runtime import AsyncRuntime
from src.services.logging_service import ServiceLogger

class OnboardCommandRejectedError(RuntimeError):
    def __init__(self, message: str, response: dict[str, Any]) -> None:
        super().__init__(message)
        self.response = dict(response)


class OnboardMissionService(OnboardMissionServiceContract):
    """Correlate onboard commands, mission events and recoverable QR state."""

    _TIMELINE_LIMIT = 200
    _QR_REPORT_HISTORY_LIMIT = 256
    _QR_REPORT_DELIVERY_ATTEMPTS = 3
    _QR_REPORT_RETRY_DELAYS_S = (0.25, 0.75)
    _TARGET_LOCK_REPORT_HISTORY_LIMIT = 256
    _TARGET_LOCK_STATUS_TIMEOUT_S = 1.0
    _HSS_TASKS = frozenset({"hss.escape", "hss.predict", "hss.transit"})
    _TERMINAL_PHASES = frozenset(TERMINAL_MISSION_PHASES)
    _CLOCK_REFRESH_TASKS = frozenset({"kamikaze.execute", "kamikaze.transit"})
    _CLOCK_REFRESH_INTERVAL_S = 10.0
    _REASON_DIAGNOSES = {
        "completed": "Görev Onboard tarafından başarıyla tamamlandı.",
        "operator.cancelled": "Görev operatör tarafından iptal edildi.",
        "operator.stopped": "Görev operatör tarafından durduruldu.",
        "operator.stop_requested": "Görev için durdurma isteği işlendi.",
        "operator.mode_released": "Gerekli uçuş modu bırakıldı veya değiştirildi.",
        "vehicle.disarmed": "Araç DISARMED olduğu için görev devam edemedi.",
        "vehicle.disconnected": "Araç/telemetri bağlantısı kesildiği için görev sonlandı.",
        "telemetry.unavailable": "Görevin ihtiyaç duyduğu telemetri veya konum verisi yok.",
        "vision.camera_unavailable": "Kamera akışı alınamadığı için görüntü görevi sonlandı.",
        "vision.unavailable": "Görüntü işleme bileşeni kullanılamıyor.",
        "runtime.timeout": "Görev tanımlı çalışma süresini aştı.",
        "navigation.hss_rejected": "HSS güvenlik/navigasyon kontrolü görevi reddetti.",
        "input.rejected": "Onboard görev girdilerinden birini reddetti; hedef, plan, mod ve kaynak doğrulamasını kontrol edin.",
        "safety.aborted": "Görev güvenlik kontrolü tarafından durduruldu.",
        "execution.failed": "Onboard görev yürütmesi başarısız oldu.",
        "execution.updated": "Onboard görev durumu güncellendi.",
    }

    def __init__(
        self,
        orin: OrinService,
        competition: CompetitionService,
        logger: ServiceLogger | None = None,
        *,
        telemetry: TelemetryService | None = None,
        async_runtime: AsyncRuntime | None = None,
    ) -> None:
        self._orin = orin
        self._competition = competition
        self._telemetry = telemetry
        self._logger = logger
        self._state = OnboardMissionState()
        self._changed = EventBus[OnboardMissionState]()
        self._events = EventBus[OnboardMissionEvent]()
        self._subscriptions: list[Subscription] = []
        self._event_cursors: dict[int, int] = {}
        self._event_keys: set[tuple[object, ...]] = set()
        self._lock = threading.RLock()
        self._started = False
        self._recovery_running = False
        self._recovery_timer: threading.Timer | None = None
        self._remote_active_observed = False
        self._async_runtime = async_runtime or AsyncRuntime()
        self._owns_async_runtime = async_runtime is None
        self._hss_observed = False
        self._hss_revision = 0
        self._hss_content_hash = ""
        self._hss_acknowledged_hash = ""
        self._hss_snapshot: tuple[HssZone, ...] = ()
        self._hss_transit_revision = 0
        self._target_source_revision = 0
        self._clock_source_revision = 0
        self._competition_clock_timer: threading.Timer | None = None
        self._competition_clock_generation = 0
        self._competition_clock_task = ""
        self._target_content_hash = ""
        self._target_acknowledged_hash = ""
        self._last_orin_session = ""
        self._submitted_qr_reports: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._submitted_target_lock_reports: OrderedDict[str, dict[str, Any]] = (
            OrderedDict()
        )
        self._target_lock_status_generation = 0
        self._target_lock_status_timer: threading.Timer | None = None
        self._target_sync_lock = threading.RLock()
        self._hss_sync_lock = threading.RLock()
        self._qr_report_lock = threading.RLock()
        self._target_lock_report_lock = threading.RLock()

    @property
    def state(self) -> OnboardMissionState:
        with self._lock:
            return replace(
                self._state,
                result=dict(self._state.result),
                target=dict(self._state.target),
                mission_contexts={
                    key: dict(value)
                    for key, value in self._state.mission_contexts.items()
                },
                hss_route=dict(self._state.hss_route),
                timeline=tuple(self._state.timeline),
            )

    @property
    def changed(self) -> EventBus[OnboardMissionState]:
        return self._changed

    @property
    def events(self) -> EventBus[OnboardMissionEvent]:
        return self._events

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self._subscriptions.extend(
            [
                self._orin.changed.subscribe(self._on_orin_changed),
                self._orin.register_handler(PacketType.MISSION_TRANSITION, self._handle_transition),
                self._orin.register_handler(PacketType.MISSION_RESULT, self._handle_result),
                self._orin.register_handler(PacketType.MISSION_EVENT, self._handle_mission_event),
                self._orin.register_handler(PacketType.MISSION_CHAIN, self._handle_chain),
                self._orin.register_handler(PacketType.KAMIKAZE_EVENT, self._handle_kamikaze_event),
                self._orin.register_handler(PacketType.KAMIKAZE_QR_REPORT, self._handle_qr_report),
                self._orin.register_handler(
                    PacketType.TARGET_LOCK_STATUS,
                    self._handle_target_lock_status,
                ),
                self._orin.register_handler(
                    PacketType.TARGET_LOCK_REPORT,
                    self._handle_target_lock_report,
                ),
                self._orin.register_handler(
                    PacketType.HSS_ROUTE_PLAN,
                    self._handle_hss_route_plan,
                ),
            ]
        )
        self._on_orin_changed(self._orin.state)

    def stop(self) -> None:
        with self._lock:
            self._started = False
            recovery_timer = self._recovery_timer
            self._recovery_timer = None
        if recovery_timer is not None:
            recovery_timer.cancel()
        self._cancel_competition_clock_refresh()
        self._clear_target_lock_status()
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        if self._owns_async_runtime:
            self._async_runtime.stop()

    def start_task(self, task: str, *, source: str = "ui") -> OrinCommandReceipt:
        canonical = self._canonical_task(task)
        if canonical == "kamikaze.transit":
            raise RuntimeError(
                "kamikaze.transit seçili P1/P2 planı olmadan başlatılamaz; "
                "KamikazeApproachService.start_selected_mission() kullanın"
            )
        with self._lock:
            active_task = self._state.active_task
            if active_task == canonical and not self._state.terminal:
                raise RuntimeError(f"Onboard görevi zaten çalışıyor: {canonical}")
        if canonical == "kamikaze.execute":
            self._sync_competition_clock(refresh=True)
            self._sync_kamikaze_target()
        elif canonical == KAMIKAZE_CHECK_TASK:
            self._sync_competition_clock(refresh=True)
        elif canonical == "target.lock":
            self._try_sync_competition_clock()
        if canonical in self._HSS_TASKS:
            self._on_hss_updated(tuple(self._competition.hss_zones))
            self._send_hss_update()
        wire_source = "ui" if source in {"ui", "qt-ui", "operator-ui"} else source
        requested_at = (
            datetime.now(UTC).isoformat().replace("+00:00", "Z")
            if canonical == KAMIKAZE_CHECK_TASK
            else ""
        )
        receipt = self._issue(
            TaskStart(canonical, wire_source, requested_at=requested_at),
            pending="start",
            task=canonical,
        )
        if canonical in self._CLOCK_REFRESH_TASKS:
            self._schedule_competition_clock_refresh(canonical)
        return receipt

    def start_kamikaze_transit(
        self,
        plan: KamikazeApproachPlan,
        *,
        source: str = "ui",
    ) -> OrinCommandReceipt:
        """Atomically publish the locked approach plan, then launch onboard transit.

        The QR target and selected approach geometry are sent without coupling
        this Kamikaze task to the independent HSS task family.
        """
        selected = plan.selected_corridor
        if selected is None or not plan.locked:
            raise RuntimeError("Kamikaze transit için önce Top-5 içinden koridor kilitlenmeli")
        with self._lock:
            if self._state.active_task == "kamikaze.transit" and not self._state.terminal:
                raise RuntimeError("Onboard kamikaze.transit görevi zaten çalışıyor")

        # Anchor Onboard first, then refresh the target without silently moving
        # the operator's locked corridor.
        self._sync_competition_clock(refresh=True)
        self._competition.refresh_qr_coordinate()
        target = self._competition.qr_coordinate
        if target is None:
            raise RuntimeError("TEKNOFEST QR hedef koordinatı alınamadı")
        locked_qr = Point(float(plan.qr.latitude), float(plan.qr.longitude), 0.0)
        current_qr = Point(float(target.latitude), float(target.longitude), 0.0)
        if locked_qr.distance_to(current_qr) > 1.0:
            raise RuntimeError(
                "QR koordinatı koridor seçiminden sonra değişti; koridorlar yeniden hesaplanmalı"
            )
        self._send_target_update(target)

        with self._lock:
            target_revision = self._target_source_revision

        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        plan_id = f"gcs-kamikaze:{plan.revision}:{selected.angle_deg}"
        draft = KamikazeApproachPlanUpdate(
            plan_id=plan_id,
            plan_revision=int(plan.revision),
            target_revision=int(target_revision),
            qr_target=HssWaypoint(
                float(plan.qr.latitude),
                float(plan.qr.longitude),
                float(plan.config.target_altitude_m),
            ),
            p2=HssWaypoint(
                float(selected.lineup.latitude),
                float(selected.lineup.longitude),
                float(selected.lineup.altitude),
            ),
            p1=HssWaypoint(
                float(selected.dive_start.latitude),
                float(selected.dive_start.longitude),
                float(selected.dive_start.altitude),
            ),
            approach_heading_deg=float(selected.inbound_heading_deg),
            dive_start_altitude_m=float(plan.config.start_altitude_m),
            pullup_altitude_m=float(plan.config.pullup_altitude_m),
            dive_pitch_deg=-abs(float(plan.config.dive_angle_deg)),
            safety_margin_m=float(plan.config.hss_margin_m),
            corridor_width_m=float(plan.config.corridor_width_m),
            hss_revision=0,
            created_at_utc=created_at,
            content_hash="pending",
        )
        encoded = json.dumps(
            draft.canonical_payload(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        approach = replace(draft, content_hash=hashlib.sha256(encoded).hexdigest())
        self._log(
            "kamikaze.approach_tx",
            "Kilitli P2/P1 yaklaşma planı Orin'e gönderiliyor",
            "info",
            {
                "plan_id": approach.plan_id,
                "plan_revision": approach.plan_revision,
                "target_revision": approach.target_revision,
                "hss_revision": approach.hss_revision,
                "angle_deg": selected.angle_deg,
            },
        )
        plan_receipt = self._send_request(approach)
        plan_execution = str(plan_receipt.response.get("execution", "")).strip().lower()
        if not plan_receipt.accepted or plan_execution not in {"updated", "unchanged"}:
            raise OnboardCommandRejectedError(
                plan_receipt.message or "Orin yaklaşma planını reddetti",
                plan_receipt.response,
            )
        self._log(
            "kamikaze.approach_ack",
            "P2/P1 yaklaşma planı Orin tarafından kabul edildi",
            "success",
            {
                "request_id": plan_receipt.request_id,
                "execution": plan_execution,
                "plan_id": approach.plan_id,
            },
        )

        wire_source = "ui" if source in {"ui", "qt-ui", "operator-ui"} else source
        receipt = self._issue(
            TaskStart(
                "kamikaze.transit",
                wire_source,
                parameters={
                    "plan_id": approach.plan_id,
                    "plan_revision": approach.plan_revision,
                },
                requested_at=created_at,
            ),
            pending="start",
            task="kamikaze.transit",
        )
        self._schedule_competition_clock_refresh("kamikaze.transit")
        return receipt

    def start_hss_transit(
        self,
        destination: Point,
        *,
        source: str = "ui",
    ) -> OrinCommandReceipt:
        """Replace the current HSS transit with one ground-selected target."""

        target = Point(
            float(destination.latitude),
            float(destination.longitude),
            float(destination.altitude),
        )
        state = self.state
        if (
            (state.active_task == "hss.transit" and not state.terminal)
            or bool(state.hss_route)
        ):
            self.cancel_task("hss.transit")

        self._on_hss_updated(tuple(self._competition.hss_zones))
        self._send_hss_update()
        with self._lock:
            revision = self._next_revision(self._hss_transit_revision)
            self._hss_transit_revision = revision
            self._state = replace(
                self._state,
                hss_route={
                    "route_id": f"pending:{revision}",
                    "phase": "transit",
                    "destination": {
                        "latitude_deg": target.latitude,
                        "longitude_deg": target.longitude,
                        "altitude_m": target.altitude,
                    },
                    "steps": [],
                },
            )
        self._publish()
        command = HssTransitRequest(
            HssWaypoint(target.latitude, target.longitude, target.altitude),
            revision,
            "ui" if source in {"ui", "qt-ui", "operator-ui"} else source,
        )
        try:
            receipt = self._send_request(command)
        except Exception:
            with self._lock:
                self._state = replace(self._state, hss_route={})
            self._publish()
            raise
        if not receipt.accepted:
            with self._lock:
                self._state = replace(self._state, hss_route={})
            self._publish()
            raise OnboardCommandRejectedError(receipt.message, receipt.response)
        return receipt

    def _sync_competition_clock(self, *, refresh: bool = False) -> OrinCommandReceipt:
        """Anchor onboard result timestamps to the latest TEKNOFEST clock."""

        server_now = (
            self._competition.refresh_server_time()
            if refresh
            else self._competition.estimated_server_time()
        )
        if not isinstance(server_now, datetime) or server_now.tzinfo is None:
            raise RuntimeError("TEKNOFEST sunucu saati henüz alınmadı")
        server_now = server_now.astimezone(UTC)
        with self._lock:
            self._clock_source_revision = self._next_revision(
                self._clock_source_revision
            )
            revision = self._clock_source_revision
        command = CompetitionClockUpdate(
            ContractCompetitionServerTime(
                day=server_now.day,
                hour=server_now.hour,
                minute=server_now.minute,
                second=server_now.second,
                millisecond=server_now.microsecond // 1_000,
            ),
            source_revision=revision,
        )
        self._log(
            "competition.clock_sync_tx",
            "TEKNOFEST sunucu saati Orin'e gönderiliyor",
            "info",
            command.to_payload(),
        )
        receipt = self._send_request(command)
        execution = str(receipt.response.get("execution", "")).strip().lower()
        if not receipt.accepted or execution not in {"updated", "unchanged"}:
            raise OnboardCommandRejectedError(
                receipt.message or "Orin TEKNOFEST sunucu saatini reddetti",
                receipt.response,
            )
        acknowledged_revision = self._optional_int(
            receipt.response.get("clock_revision")
        )
        if acknowledged_revision is not None and acknowledged_revision != revision:
            raise OnboardCommandRejectedError(
                "Orin saat ACK revizyonu gönderilen revizyonla uyuşmuyor",
                receipt.response,
            )
        self._log(
            "competition.clock_sync_ack",
            "TEKNOFEST sunucu saati Orin tarafından kabul edildi",
            "success",
            {
                "request_id": receipt.request_id,
                "execution": execution,
                "clock_revision": revision,
            },
        )
        return receipt

    def _try_sync_competition_clock(self) -> OrinCommandReceipt | None:
        """Send a clock sample when available without gating an Onboard task."""
        try:
            return self._sync_competition_clock()
        except Exception as exc:
            self._log(
                "competition.clock_sync_skipped",
                "TEKNOFEST saati kullanılamadı; Onboard görevi yine de başlatılıyor",
                "warning",
                {"error": str(exc), "error_type": type(exc).__name__},
            )
            return None

    def _schedule_competition_clock_refresh(self, task: str) -> None:
        """Refresh the accepted TEKNOFEST clock only while a long mission runs."""
        canonical = self._canonical_task(task)
        if canonical not in self._CLOCK_REFRESH_TASKS:
            return
        with self._lock:
            self._competition_clock_generation += 1
            generation = self._competition_clock_generation
            previous = self._competition_clock_timer
            self._competition_clock_task = canonical

            def refresh() -> None:
                with self._lock:
                    if generation != self._competition_clock_generation:
                        return
                    self._competition_clock_timer = None
                    active = (
                        self._started
                        and self._state.active_task == canonical
                        and not self._state.terminal
                    )
                if not active:
                    return
                try:
                    self._sync_competition_clock()
                except Exception as exc:
                    self._log(
                        "competition.clock_refresh_failed",
                        "Aktif kamikaze görevi için TEKNOFEST saat yenilemesi yapılamadı",
                        "warning",
                        {"task": canonical, "error": str(exc), "error_type": type(exc).__name__},
                    )
                self._schedule_competition_clock_refresh(canonical)

            timer = threading.Timer(self._CLOCK_REFRESH_INTERVAL_S, refresh)
            timer.daemon = True
            self._competition_clock_timer = timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _cancel_competition_clock_refresh(self, task: str | None = None) -> None:
        with self._lock:
            if task is not None and self._competition_clock_task != task:
                return
            self._competition_clock_generation += 1
            timer = self._competition_clock_timer
            self._competition_clock_timer = None
            self._competition_clock_task = ""
        if timer is not None:
            timer.cancel()

    def _sync_kamikaze_target(self) -> OrinCommandReceipt | None:
        """Forward an already-known QR target without contacting TEKNOFEST.

        Competition polling belongs to its own service.  Starting an Onboard
        task must never synchronously depend on that service or its network.
        """
        target = self._competition.qr_coordinate
        if target is None:
            self._log(
                "kamikaze.target_sync_skipped",
                "Kamikaze hedefi yok; görev Onboard'a mevcut hedef durumuyla gönderiliyor",
                "warning",
                {},
            )
            return None
        try:
            return self._send_target_update(target)
        except Exception as exc:
            self._record_target_failure(exc)
            raise

    def _send_target_update(self, target: QrCoordinate) -> OrinCommandReceipt | None:
        with self._target_sync_lock:
            canonical = {
                "latitude_deg": round(float(target.latitude), 7),
                "longitude_deg": round(float(target.longitude), 7),
                "plate_height_m": 3.0,
                "target_id": "teknofest-qr-target",
            }
            content_hash = self._content_hash(canonical)
            with self._lock:
                if content_hash != self._target_content_hash:
                    self._target_source_revision = self._next_revision(
                        self._target_source_revision
                    )
                    self._target_content_hash = content_hash
                source_revision = self._target_source_revision
                if (
                    content_hash == self._target_acknowledged_hash
                    and self._state.target_ready
                ):
                    return None
            command = KamikazeTargetUpdate(
                latitude_deg=canonical["latitude_deg"],
                longitude_deg=canonical["longitude_deg"],
                plate_height_m=canonical["plate_height_m"],
                target_id=canonical["target_id"],
                source="teknofest",
                source_revision=source_revision,
                content_hash=content_hash,
                fetched_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            payload = command.to_payload()
            self._log(
                "kamikaze.target_tx",
                "TEKNOFEST QR hedefi Orin'e gönderiliyor",
                "info",
                dict(payload),
            )
            receipt = self._send_request(command)
            execution = str(receipt.response.get("execution", "")).strip().lower()
            if not receipt.accepted:
                raise OnboardCommandRejectedError(
                    f"Kamikaze hedefi Orin tarafından reddedildi: {receipt.message}",
                    receipt.response,
                )
            if execution not in {"updated", "unchanged"}:
                raise OnboardCommandRejectedError(
                    "Orin kamikaze hedefi için geçersiz ACK döndürdü",
                    receipt.response,
                )
            accepted_target = receipt.response.get("target")
            compact_ack = not isinstance(accepted_target, dict)
            if compact_ack:
                # Current onboard releases may omit the echoed target to keep
                # the correlated ACK inside one low-bandwidth MAVLink frame.
                # The ACK still proves acceptance; retain the exact snapshot
                # that produced this request instead of rejecting it locally.
                accepted_target = {
                    **canonical,
                    "plate_height_m": 3.0,
                }
            else:
                accepted_target = dict(accepted_target)
            revision = self._optional_int(receipt.response.get("target_revision"))
            if revision is not None and revision != source_revision:
                raise OnboardCommandRejectedError(
                    "Orin ACK yanıtındaki hedef revizyonu gönderilen revizyonla uyuşmuyor",
                    receipt.response,
                )
            if revision is None:
                revision = source_revision
            try:
                accepted_coordinates = (
                    round(float(accepted_target["latitude_deg"]), 7),
                    round(float(accepted_target["longitude_deg"]), 7),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise OnboardCommandRejectedError(
                    "Orin ACK yanıtındaki hedef koordinatları geçersiz",
                    receipt.response,
                ) from exc
            if accepted_coordinates != (
                canonical["latitude_deg"],
                canonical["longitude_deg"],
            ):
                raise OnboardCommandRejectedError(
                    "Orin farklı bir kamikaze hedefini kabul ettiğini bildirdi",
                    receipt.response,
                )
            with self._lock:
                self._target_acknowledged_hash = content_hash
                self._state = replace(
                    self._state,
                    target_ready=True,
                    target_revision=revision,
                    target=dict(accepted_target),
                    last_error="",
                )
            self._log(
                "kamikaze.target_ack",
                "Kamikaze hedefi Orin tarafından kabul edildi",
                "success",
                {
                    "request_id": receipt.request_id,
                    "execution": execution,
                    "target_revision": revision,
                    "target": dict(accepted_target),
                    "compact_ack": compact_ack,
                },
            )
            self._publish()
            return receipt

    def _record_target_failure(self, exc: Exception) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                target_ready=False,
                last_error=str(exc),
            )
        self._log(
            "kamikaze.target_failed",
            f"Kamikaze hedefi Orin'e aktarılamadı: {exc}",
            "danger",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
        self._publish()

    def _on_hss_updated(self, zones: tuple[HssZone, ...]) -> None:
        snapshot = tuple(sorted(zones, key=lambda zone: zone.identifier))
        payload_zones = self._hss_payload(snapshot)
        content_hash = self._content_hash(
            {"active": bool(snapshot), "zones": payload_zones}
        )
        with self._lock:
            self._hss_observed = True
            self._hss_snapshot = snapshot
            if content_hash != self._hss_content_hash:
                self._hss_revision = self._next_revision(self._hss_revision)
                self._hss_content_hash = content_hash

    def _send_hss_update(self) -> OrinCommandReceipt | None:
        with self._hss_sync_lock:
            with self._lock:
                zones = self._hss_snapshot
                revision = self._hss_revision
                content_hash = self._hss_content_hash
                if content_hash == self._hss_acknowledged_hash:
                    return None
            active = bool(zones)
            command = ContractHssUpdate(
                revision=revision,
                active=active,
                content_hash=content_hash,
                zones=tuple(
                    ContractHssZone(
                        int(zone.identifier),
                        float(zone.latitude),
                        float(zone.longitude),
                        float(zone.radius_m),
                    )
                    for zone in zones
                ),
                reason="source-active" if active else "source-ended",
            )
            payload = command.to_payload()
            self._log(
                "competition.hss_sync_tx",
                "TEKNOFEST HSS durumu Orin'e gönderiliyor",
                "info",
                {
                    "revision": revision,
                    "active": active,
                    "hss_count": len(zones),
                    "content_hash": content_hash,
                },
            )
            receipt = self._send_request(command)
            execution = str(receipt.response.get("execution", "")).strip().lower()
            accepted_executions = {"updated", "unchanged"}
            if not active:
                accepted_executions.add("cleared")
            if not receipt.accepted or execution not in accepted_executions:
                raise OnboardCommandRejectedError(
                    receipt.message or "Orin HSS güncellemesini reddetti",
                    receipt.response,
                )
            acknowledged_revision = self._optional_int(
                receipt.response.get("revision")
            )
            if acknowledged_revision is not None and acknowledged_revision != revision:
                raise OnboardCommandRejectedError(
                    "Orin farklı bir HSS revizyonunu onayladı",
                    receipt.response,
                )
            with self._lock:
                self._hss_acknowledged_hash = content_hash
                self._state = replace(
                    self._state,
                    hss_ready=True,
                    hss_active=active,
                    hss_revision=revision,
                    hss_count=len(zones),
                    hss_content_hash=content_hash,
                )
            self._log(
                "competition.hss_sync_ack",
                "HSS durumu Orin tarafından kabul edildi",
                "success",
                {
                    "request_id": receipt.request_id,
                    "execution": execution,
                    "revision": revision,
                    "hss_count": len(zones),
                },
            )
            self._publish()
            return receipt

    @staticmethod
    def _hss_payload(zones: tuple[HssZone, ...]) -> list[dict[str, int | float]]:
        return [
            {
                "id": int(zone.identifier),
                "latitude_deg": round(float(zone.latitude), 7),
                "longitude_deg": round(float(zone.longitude), 7),
                "radius_m": round(float(zone.radius_m), 2),
            }
            for zone in zones
        ]

    @staticmethod
    def _content_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    @staticmethod
    def _next_revision(current: int) -> int:
        """Keep source revisions increasing even when the YKİ process restarts."""

        epoch_milliseconds = int(datetime.now(UTC).timestamp() * 1_000)
        return max(int(current) + 1, epoch_milliseconds)

    def pause_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control(TaskPause(task), task, "pause")

    def resume_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control(TaskResume(task), task, "resume")

    def stop_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control(TaskStop(task), task, "stop")

    def cancel_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        canonical = self._canonical_task(task)
        try:
            receipt = self._task_control(TaskCancel(canonical), canonical, "cancel")
        except OnboardCommandRejectedError as exc:
            # Cancellation is idempotent for the operator. A lost start ACK
            # may leave YKİ uncertain even though onboard never instantiated
            # the task; MissionNotFound means the desired end state is met.
            if str(exc.response.get("error_type", "")) != "MissionNotFoundError":
                raise
            request_id = self._optional_int(exc.response.get("request_id")) or 0
            message = "Görev zaten aktif değildi; iptal edilmiş kabul edildi"
            response = {
                **exc.response,
                "accepted": True,
                "execution": "cancelled",
                "task": canonical,
                "already_inactive": True,
            }
            with self._lock:
                self._state = replace(
                    self._state,
                    active_task=canonical,
                    mission_id=None,
                    phase="cancelled",
                    waiting_for_guided=False,
                    last_error="",
                )
            self._log(
                "onboard.cancel_idempotent",
                message,
                "success",
                {
                    "task": canonical,
                    "request_id": request_id,
                    "onboard_error": str(exc),
                },
            )
            self._publish()
            receipt = OrinCommandReceipt(
                request_id,
                True,
                message,
                response,
            )
        if canonical == "hss.transit":
            with self._lock:
                self._state = replace(self._state, hss_route={})
            self._publish()
        return receipt

    def refresh(self) -> OrinCommandReceipt:
        receipt = self._issue(MissionList(), pending="refresh")
        self._apply_mission_list(receipt.response)
        return receipt

    def mission_status(
        self,
        *,
        mission_id: int | None = None,
        task: str | None = None,
    ) -> OrinCommandReceipt:
        if mission_id is None and not task:
            raise ValueError("Mission ID veya görev adı gerekli")
        command = MissionQuery(
            mission_id=int(mission_id) if mission_id is not None else None,
            task=self._canonical_task(str(task)) if task else "",
        )
        receipt = self._issue(command, pending="status")
        return receipt

    def mission_events(
        self,
        mission_id: int,
        *,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> OrinCommandReceipt:
        if not 1 <= int(limit) <= 500:
            raise ValueError("Event limiti 1..500 aralığında olmalı")
        cursor = self._event_cursors.get(int(mission_id), 0)
        if after_sequence is not None:
            cursor = max(0, int(after_sequence))
        receipt = self._issue(
            MissionEventsQuery(
                mission_ids=(int(mission_id),),
                minimum_level="DEBUG",
                after_sequence=cursor,
                limit=int(limit),
            ),
            pending="events",
        )
        self._apply_recovered_events(int(mission_id), receipt.response.get("events", []))
        return receipt

    def start_chain(self, chain: str) -> OrinCommandReceipt:
        chain_id = str(chain).strip()
        if not chain_id:
            raise ValueError("Chain kimliği boş olamaz")
        return self._issue(ChainStart(chain_id), pending="chain")

    def set_video_recording(self, enabled: bool) -> OrinCommandReceipt:
        """Control only Onboard's video recording through the shared contract.

        Telemetry and session logging deliberately have no coupling to this
        control; their lifecycle is owned by the Onboard runtime.
        """

        command = RecordingControl("video", bool(enabled))
        desired = bool(enabled)
        self._log(
            "onboard.recording_control_dispatch",
            f"Onboard video kaydı {'başlatılıyor' if desired else 'durduruluyor'}",
            "info",
            {"stream": command.stream, "enabled": desired},
        )
        try:
            receipt = self._send_request(command)
            if not receipt.accepted:
                raise OnboardCommandRejectedError(receipt.message, receipt.response)
        except Exception as exc:
            self._log(
                "onboard.recording_control_failed",
                f"Onboard video kayıt komutu uygulanamadı: {exc}",
                "danger",
                {"stream": command.stream, "enabled": desired},
            )
            raise

        video_enabled = bool(receipt.response.get("video", desired))
        telemetry_enabled = bool(receipt.response.get("telemetry", True))
        self._log(
            "onboard.recording_control_acknowledged",
            f"Onboard video kaydı {'açık' if video_enabled else 'kapalı'}",
            "success",
            {
                "stream": command.stream,
                "video": video_enabled,
                "telemetry": telemetry_enabled,
                "request_id": receipt.request_id,
            },
        )
        return receipt

    def _task_control(
        self,
        command: ContractModel,
        task: str,
        pending: str,
    ) -> OrinCommandReceipt:
        canonical = self._canonical_task(task)
        return self._issue(
            command,
            pending=pending,
            task=canonical,
        )

    def _send_request(self, command: ContractModel) -> OrinCommandReceipt:
        """Send a validated shared contract through the host MAVLink adapter."""

        return self._orin.issue(command.packet_type, command.to_payload())

    def _issue(
        self,
        command: ContractModel,
        *,
        pending: str,
        task: str = "",
    ) -> OrinCommandReceipt:
        packet_type = command.packet_type
        payload = command.to_payload()
        with self._lock:
            if self._state.pending_command:
                raise RuntimeError(f"Onboard komutu zaten bekleniyor: {self._state.pending_command}")
            starting = packet_type == "task.start"
            starting_kamikaze = starting and task in {
                "kamikaze.execute",
                "kamikaze.transit",
                KAMIKAZE_CHECK_TASK,
            }
            starting_actuator_test = starting and task == ACTUATOR_TEST_TASK
            self._state = replace(
                self._state,
                pending_command=pending,
                last_error="",
                active_task=task or self._state.active_task,
                mission_id=(None if starting else self._state.mission_id),
                phase=("requesting" if starting else self._state.phase),
                reason_code=("" if starting else self._state.reason_code),
                qr_status=("pending" if starting_kamikaze else self._state.qr_status),
                qr_text=(None if starting_kamikaze else self._state.qr_text),
                qr_event=("" if starting_kamikaze else self._state.qr_event),
                qr_time_utc=("" if starting_kamikaze else self._state.qr_time_utc),
                qr_conflicts=(() if starting_kamikaze else self._state.qr_conflicts),
                critical_event=("" if starting else self._state.critical_event),
                result=({} if starting else self._state.result),
                acknowledgement_snapshot=(
                    {} if starting else self._state.acknowledgement_snapshot
                ),
                result_received=(False if starting else self._state.result_received),
                actuator_step=("" if starting_actuator_test else self._state.actuator_step),
                actuator_step_count=(
                    0 if starting_actuator_test else self._state.actuator_step_count
                ),
                actuator_servo_output=(
                    None
                    if starting_actuator_test
                    else self._state.actuator_servo_output
                ),
                actuator_pwm=(None if starting_actuator_test else self._state.actuator_pwm),
                actuator_armed=(
                    None if starting_actuator_test else self._state.actuator_armed
                ),
                actuator_notification_only=(
                    False
                    if starting_actuator_test
                    else self._state.actuator_notification_only
                ),
                timeline=(() if starting else self._state.timeline),
            )
            if starting:
                self._event_keys.clear()
        self._publish()
        try:
            self._log(
                "onboard.command_dispatch",
                f"Onboard komutu gönderiliyor: {packet_type}",
                "warning" if packet_type == "task.cancel" else "info",
                {
                    "type": packet_type,
                    "task": task,
                    "payload": dict(payload),
                    "pending": pending,
                },
            )
            receipt = self._send_request(command)
            if not receipt.accepted:
                raise OnboardCommandRejectedError(
                    receipt.message,
                    receipt.response,
                )
            if packet_type.startswith("task.") or packet_type == "mission.status":
                self._apply_ack(receipt.response, task=task)
            if packet_type == "task.cancel":
                self._log(
                    "onboard.cancel_acknowledged",
                    "Onboard görev iptali doğrulandı",
                    "success",
                    {
                        "task": task,
                        "request_id": receipt.request_id,
                        "execution": receipt.response.get("execution", ""),
                        "mission_id": receipt.response.get("mission_id"),
                        "already_inactive": bool(
                            receipt.response.get("already_inactive", False)
                        ),
                    },
                )
            if packet_type == "task.start":
                # A mission may finish before its transition/result packets
                # cross a busy MAVLink route. Reconcile once after the ACK so
                # the UI cannot remain on "starting" forever.
                self._schedule_recovery(2.0)
            return receipt
        except OnboardCommandRejectedError as exc:
            self._set_error(
                str(exc),
                phase="failed" if packet_type == "task.start" else None,
            )
            self._log(
                "onboard.command_failed",
                str(exc),
                "danger",
                {
                    "type": packet_type,
                    "error_type": exc.response.get("error_type", type(exc).__name__),
                    "response": exc.response,
                },
            )
            raise
        except TimeoutError as exc:
            if packet_type == "task.start" and self._task_start_was_observed(task):
                observed = self.state
                message = (
                    "task.start ACK'i zamanında gelmedi; ancak Onboard görev "
                    f"geçişini doğruladı ({observed.phase})."
                )
                self._log(
                    "onboard.command_ack_missing_mission_observed",
                    message,
                    "warning",
                    {
                        "type": packet_type,
                        "task": task,
                        "mission_id": observed.mission_id,
                        "phase": observed.phase,
                    },
                )
                self._schedule_recovery(2.0)
                return OrinCommandReceipt(
                    0,
                    True,
                    message,
                    {
                        "task": task,
                        "mission_id": observed.mission_id,
                        "execution": observed.phase,
                        "observed_lifecycle": True,
                    },
                )
            # A lost ACK does not prove that task.start was rejected. Never retry it.
            message = (
                "Komut yanıtı zaman aşımına uğradı; aynı görev otomatik "
                "başlatılmadı. Görev durumu yeniden sorgulanacak."
            )
            self._set_error(
                message,
                phase=("reconciling" if packet_type.startswith("task.") else None),
            )
            self._log("onboard.command_timeout", message, "warning", {"type": packet_type})
            self._start_recovery()
            raise TimeoutError(message) from exc
        except Exception as exc:
            self._set_error(str(exc))
            self._log(
                "onboard.command_failed",
                f"Onboard komutu başarısız: {packet_type}",
                "danger",
                {"type": packet_type, "error": str(exc), "error_type": type(exc).__name__},
            )
            raise
        finally:
            with self._lock:
                self._state = replace(self._state, pending_command="")
            self._publish()

    def _task_start_was_observed(self, task: str) -> bool:
        state = self.state
        return bool(
            task
            and state.active_task == task
            and state.mission_id is not None
            and state.phase in {"starting", "running", "pausing", "paused", "stopping"}
        )

    def _apply_ack(self, payload: dict[str, Any], *, task: str = "") -> None:
        snapshot = payload.get("snapshot")
        snapshot_payload = dict(snapshot) if isinstance(snapshot, dict) else {}
        mission_id = self._optional_int(payload.get("mission_id"))
        phase = str(payload.get("execution", "acknowledged")).strip().lower()
        if snapshot_payload:
            mission_id = self._optional_int(snapshot_payload.get("mission_id")) or mission_id
            phase = str(snapshot_payload.get("phase", phase)).strip().lower()
        waiting = bool(payload.get("waiting_for_mode", False))
        vehicle_mode = str(
            payload.get(
                "vehicle_mode",
                snapshot_payload.get("vehicle_mode", self._state.vehicle_mode),
            )
        ).upper()
        already_inactive = bool(payload.get("already_inactive", False))
        with self._lock:
            acknowledged_task = str(
                payload.get("task") or task or self._state.active_task
            ).strip()
            canonical_task = (
                self._canonical_task(acknowledged_task)
                if acknowledged_task
                else self._state.active_task
            )
            contexts = self._updated_mission_contexts(
                canonical_task,
                mission_id,
                "cancelled" if already_inactive else phase,
            )
            self._state = replace(
                self._state,
                active_task=canonical_task,
                mission_id=(None if already_inactive else mission_id or self._state.mission_id),
                mission_contexts=contexts,
                target_lock_mission_id=(
                    mission_id
                    if canonical_task == "target.lock" and not already_inactive
                    else self._state.target_lock_mission_id
                ),
                target_lock_phase=(
                    ("cancelled" if already_inactive else phase)
                    if canonical_task == "target.lock"
                    else self._state.target_lock_phase
                ),
                phase=("cancelled" if already_inactive else phase or self._state.phase),
                waiting_for_guided=(False if already_inactive else waiting),
                vehicle_mode=vehicle_mode,
                acknowledgement_snapshot=snapshot_payload,
            )
        message = (
            "Görev alındı, GUIDED modu bekleniyor"
            if waiting
            else "Onboard komutu kabul edildi; nihai sonuç bekleniyor"
        )
        self._log(
            "onboard.command_acknowledged",
            message,
            "warning" if waiting else "success",
            {
                "task": task,
                "mission_id": mission_id,
                "execution": phase,
                "vehicle_mode": vehicle_mode,
                "snapshot": snapshot_payload,
            },
        )
        self._publish()

    def _updated_mission_contexts(
        self,
        task: str,
        mission_id: int | None,
        phase: str,
        reason_code: str = "",
    ) -> dict[str, dict[str, Any]]:
        contexts = {
            key: dict(value) for key, value in self._state.mission_contexts.items()
        }
        if task and mission_id is not None:
            current = contexts.get(f"{task}:{mission_id}", {})
            contexts[f"{task}:{mission_id}"] = {
                **current,
                "task": task,
                "mission_id": mission_id,
                "phase": str(phase).strip().lower() or "unknown",
                "reason_code": str(reason_code).strip().lower(),
            }
        return contexts

    def _is_stale_mission_update(
        self,
        task: str,
        mission_id: int | None,
        phase: str,
    ) -> bool:
        """Keep a terminal mission outcome immutable against late packets."""
        if not task or mission_id is None:
            return False
        context = self._state.mission_contexts.get(f"{task}:{mission_id}")
        if context is None:
            return False
        previous = str(context.get("phase", "")).strip().lower()
        return previous in self._TERMINAL_PHASES and phase != previous

    def _handle_hss_route_plan(self, message: OrinMessage) -> dict[str, Any]:
        try:
            route = HssRoutePlan.from_payload(message.payload)
        except (TypeError, ValueError) as exc:
            self._log(
                "hss.route_decode_failed",
                "Onboard HSS rotası sözleşmeye uygun değil",
                "warning",
                {"error": str(exc)},
            )
            return {}
        payload = route.to_payload()
        with self._lock:
            self._state = replace(self._state, hss_route=dict(payload))
        self._log(
            "hss.route_received",
            "HSS'den kaçınan rota Onboard'dan alındı",
            "success",
            {
                "route_id": route.route_id,
                "phase": route.phase,
                "step_count": len(route.steps),
            },
        )
        self._publish()
        return {"accepted": True, "route_id": route.route_id}

    def _handle_transition(self, message: OrinMessage) -> dict[str, Any]:
        try:
            transition = MissionTransition.from_payload(message.payload)
        except (TypeError, ValueError) as exc:
            self._log(
                "mission.contract_decode_failed",
                "Onboard görev geçişi sözleşmeye uygun değil",
                "warning",
                {"packet_type": PacketType.MISSION_TRANSITION.value, "error": str(exc)},
            )
            return {}
        payload = transition.to_payload()
        current = transition.current
        mission_id = transition.mission_id
        task = transition.task
        reason = transition.reason
        reason_code = transition.reason_code
        if not self._valid_mission_identity(task, mission_id):
            self._log_ignored_mission_packet("mission.transition", task, mission_id)
            return {}
        with self._lock:
            if self._is_stale_mission_update(task, mission_id, current):
                self._log_ignored_mission_packet("mission.transition", task, mission_id)
                return {}
            contexts = self._updated_mission_contexts(
                task, mission_id, current, reason_code
            )
            updates_foreground = not self._state.active_task or task == self._state.active_task
            self._state = replace(
                self._state,
                active_task=(task if updates_foreground else self._state.active_task),
                mission_id=(mission_id if updates_foreground else self._state.mission_id),
                mission_contexts=contexts,
                target_lock_mission_id=(
                    mission_id if task == "target.lock" else self._state.target_lock_mission_id
                ),
                target_lock_phase=(
                    current if task == "target.lock" else self._state.target_lock_phase
                ),
                phase=(current if updates_foreground else self._state.phase),
                reason_code=(
                    reason_code if updates_foreground else self._state.reason_code
                ),
                waiting_for_guided=(
                    current == "starting" and self._state.waiting_for_guided
                    if updates_foreground
                    else self._state.waiting_for_guided
                ),
                last_error=(
                    self._reason_diagnosis(reason_code, reason)
                    if updates_foreground and current == "failed"
                    else self._state.last_error
                ),
            )
        self._log(
            "mission.transition",
            f"Onboard görev geçişi: {payload.get('previous', '-')} → {current}",
            "danger" if current == "failed" else "success" if current == "succeeded" else "info",
            dict(payload),
        )
        if current in self._TERMINAL_PHASES:
            self._cancel_competition_clock_refresh(task)
            self._log_terminal_diagnosis(task, mission_id, current, reason_code, reason)
            if task == "hss.transit":
                with self._lock:
                    self._state = replace(self._state, hss_route={})
        self._publish()
        return {}

    def _handle_result(self, message: OrinMessage) -> dict[str, Any]:
        try:
            contract_result = MissionResult.from_payload(message.payload)
        except (TypeError, ValueError) as exc:
            # Older onboard builds did not include mission identity in compact
            # results. Keep that migration path local; current packets always
            # take the typed shared-contract path above.
            if "task" in message.payload or "mission_id" in message.payload:
                self._log(
                    "mission.contract_decode_failed",
                    "Onboard görev sonucu sözleşmeye uygun değil",
                    "warning",
                    {"packet_type": PacketType.MISSION_RESULT.value, "error": str(exc)},
                )
                return {}
            return self._handle_legacy_result(message)
        payload: dict[str, Any] = dict(contract_result.to_payload())
        snapshot: dict[str, Any] = dict(payload["snapshot"])
        result: dict[str, Any] = (
            dict(snapshot["result"])
            if isinstance(snapshot.get("result"), dict)
            else {}
        )
        execution = contract_result.execution
        mission_id = contract_result.mission_id
        task = contract_result.task
        reason = contract_result.reason
        reason_code = contract_result.reason_code
        if not self._valid_mission_identity(task, mission_id):
            self._log_ignored_mission_packet("mission.result", task, mission_id)
            return {}
        with self._lock:
            if self._is_stale_mission_update(task, mission_id, execution):
                self._log_ignored_mission_packet("mission.result", task, mission_id)
                return {}
            contexts = self._updated_mission_contexts(
                task, mission_id, execution, reason_code
            )
            updates_foreground = not self._state.active_task or task == self._state.active_task
            self._state = replace(
                self._state,
                active_task=(task if updates_foreground else self._state.active_task),
                mission_id=(mission_id if updates_foreground else self._state.mission_id),
                mission_contexts=contexts,
                target_lock_mission_id=(
                    mission_id if task == "target.lock" else self._state.target_lock_mission_id
                ),
                target_lock_phase=(
                    execution if task == "target.lock" else self._state.target_lock_phase
                ),
                phase=(execution if updates_foreground else self._state.phase),
                reason_code=(
                    reason_code if updates_foreground else self._state.reason_code
                ),
                waiting_for_guided=(False if updates_foreground else self._state.waiting_for_guided),
                result=(
                    dict(snapshot) if updates_foreground else dict(self._state.result)
                ),
                result_received=(True if updates_foreground else self._state.result_received),
                last_error=(
                    self._reason_diagnosis(reason_code, reason)
                    if updates_foreground and execution == "failed"
                    else self._state.last_error
                ),
            )
        self._accept_result_qr(result, mission_id)
        self._log_mission_result(execution, task, mission_id, snapshot, reason, reason_code)
        if execution in self._TERMINAL_PHASES:
            self._cancel_competition_clock_refresh(task)
            self._log_terminal_diagnosis(task, mission_id, execution, reason_code, reason)
        self._publish()
        return {}

    def _handle_legacy_result(self, message: OrinMessage) -> dict[str, Any]:
        """Compatibility for result packets emitted before mission identities."""
        payload: dict[str, Any] = dict(message.payload)
        snapshot: dict[str, Any] = (
            dict(payload["snapshot"])
            if isinstance(payload.get("snapshot"), dict)
            else {}
        )
        result: dict[str, Any] = (
            dict(snapshot["result"])
            if isinstance(snapshot.get("result"), dict)
            else {}
        )
        if not result and isinstance(payload.get("result"), dict):
            result = dict(payload["result"])
        execution = str(payload.get("execution") or snapshot.get("phase") or "failed").lower()
        mission_id = self._optional_int(payload.get("mission_id") or snapshot.get("mission_id"))
        task_value = str(
            payload.get("task") or snapshot.get("task") or self.state.active_task
        ).strip()
        task = self._canonical_task(task_value) if task_value else ""
        if (task or mission_id is not None) and not self._valid_mission_identity(
            task, mission_id
        ):
            self._log_ignored_mission_packet("mission.result", task, mission_id)
            return {}
        with self._lock:
            contexts = self._updated_mission_contexts(task, mission_id, execution)
            updates_foreground = not self._state.active_task or task == self._state.active_task
            self._state = replace(
                self._state,
                active_task=(task if updates_foreground else self._state.active_task),
                mission_id=(mission_id if updates_foreground else self._state.mission_id),
                mission_contexts=contexts,
                target_lock_mission_id=(
                    mission_id if task == "target.lock" else self._state.target_lock_mission_id
                ),
                target_lock_phase=(
                    execution if task == "target.lock" else self._state.target_lock_phase
                ),
                phase=(execution if updates_foreground else self._state.phase),
                waiting_for_guided=(False if updates_foreground else self._state.waiting_for_guided),
                result=(
                    (dict(snapshot) if snapshot else dict(payload))
                    if updates_foreground
                    else dict(self._state.result)
                ),
                result_received=(True if updates_foreground else self._state.result_received),
                last_error=(
                    str(snapshot.get("reason") or payload.get("reason") or "")
                    if updates_foreground and execution == "failed"
                    else self._state.last_error
                ),
            )
        self._accept_result_qr(result, mission_id)
        self._log_mission_result(execution, task, mission_id, snapshot, "", "")
        self._publish()
        return {}

    def _accept_result_qr(self, result: Mapping[str, Any], mission_id: int | None) -> None:
        qr_text = result.get("qr_text")
        if qr_text is not None and str(qr_text):
            self._accept_qr(
                OnboardMissionEvent(
                    event="QR_DECODED",
                    mission_id=mission_id,
                    time_utc=str(result.get("end_time_utc", "")),
                    qr_text=str(qr_text),
                    source="result",
                    fields=dict(result),
                )
            )
    def _log_mission_result(
        self,
        execution: str,
        task: str,
        mission_id: int | None,
        snapshot: Mapping[str, Any],
        reason: str,
        reason_code: str,
    ) -> None:
        self._log(
            "mission.result",
            f"Onboard görev sonucu: {execution}",
            "success" if execution == "succeeded" else "warning" if execution == "cancelled" else "danger",
            {
                "mission_id": mission_id,
                "task": task,
                "snapshot": dict(snapshot),
                "reason": reason,
                "reason_code": reason_code,
            },
        )

    @classmethod
    def _reason_diagnosis(cls, reason_code: str, reason: str) -> str:
        code = str(reason_code).strip().lower()
        detail = str(reason).strip()
        summary = cls._REASON_DIAGNOSES.get(
            code,
            f"Onboard bilinmeyen görev durum kodu bildirdi: {code or 'belirtilmedi'}.",
        )
        return f"{summary} Onboard ayrıntısı: {detail}" if detail and detail != code else summary

    def _log_terminal_diagnosis(
        self,
        task: str,
        mission_id: int | None,
        phase: str,
        reason_code: str,
        reason: str,
    ) -> None:
        state = self.state
        diagnosis = self._reason_diagnosis(reason_code, reason)
        self._log(
            "mission.terminal_diagnosis",
            f"Onboard görev tanısı ({task or '-'}): {diagnosis}",
            "success" if phase == "succeeded" else "warning" if phase in {"cancelled", "stopped"} else "danger",
            {
                "task": task,
                "mission_id": mission_id,
                "phase": phase,
                "reason_code": reason_code,
                "onboard_reason": reason,
                "diagnosis": diagnosis,
                "vehicle_mode": state.vehicle_mode,
                "waiting_for_guided": state.waiting_for_guided,
                "target_ready": state.target_ready,
                "target_revision": state.target_revision,
                "hss_ready": state.hss_ready,
                "connected": state.connected,
            },
        )

    def _handle_chain(self, message: OrinMessage) -> dict[str, Any]:
        self._log("mission.chain", "Onboard görev zinciri güncellendi", "info", dict(message.payload))
        return {}

    def _handle_kamikaze_event(self, message: OrinMessage) -> dict[str, Any]:
        payload = dict(message.payload)
        payload.setdefault("task", "kamikaze.execute")
        self._process_event_payload(payload, source="live")
        return {}

    def _handle_qr_report(self, message: OrinMessage) -> OrinHandlerResponse:
        with self._qr_report_lock:
            response = self._submit_qr_report(message)
        return OrinHandlerResponse(
            response,
            message="QR report accepted",
        )

    def _handle_target_lock_status(self, message: OrinMessage) -> dict[str, Any]:
        status = TargetLockStatus.from_payload(dict(message.payload))
        tracking = status.target_id is not None
        track = TargetTrack(
            locked=tracking,
            center_x=status.center_x_px if tracking else 0,
            center_y=status.center_y_px if tracking else 0,
            width=status.width_px if tracking else 0,
            height=status.height_px if tracking else 0,
            vehicle_source="orin-target-lock" if tracking else "",
        )
        if self._telemetry is not None:
            self._telemetry.patch(target_track=track)
        with self._lock:
            previous = self._target_lock_status_timer
            self._target_lock_status_generation += 1
            generation = self._target_lock_status_generation
            timer = threading.Timer(
                self._TARGET_LOCK_STATUS_TIMEOUT_S,
                self._expire_target_lock_status,
                args=(generation,),
            )
            timer.daemon = True
            self._target_lock_status_timer = timer
        if previous is not None:
            previous.cancel()
        timer.start()
        if status.locked:
            self._log(
                "target_lock.status_locked",
                "Orin dört saniyelik hedef kilidini tamamladı",
                "success",
                {
                    "target_id": status.target_id,
                    "frame_sequence": status.frame_sequence,
                    "center_x": status.center_x_px,
                    "center_y": status.center_y_px,
                    "width": status.width_px,
                    "height": status.height_px,
                },
            )
        return {}

    def _handle_target_lock_report(
        self,
        message: OrinMessage,
    ) -> OrinHandlerResponse:
        with self._target_lock_report_lock:
            response = self._submit_target_lock_report(message)
        return OrinHandlerResponse(
            response,
            message="Target lock report accepted",
        )

    def _submit_target_lock_report(self, message: OrinMessage) -> dict[str, Any]:
        report = TargetLockReport.from_payload(dict(message.payload))
        with self._target_lock_report_lock:
            previous = self._submitted_target_lock_reports.get(report.report_id)
            if previous is not None:
                return dict(previous)
            response = {
                "execution": "queued",
                "report_id": report.report_id,
                "mission_id": report.mission_id,
                "target_id": report.target_id,
            }
            self._submitted_target_lock_reports[report.report_id] = dict(response)
            self._submitted_target_lock_reports.move_to_end(report.report_id)
            while (
                len(self._submitted_target_lock_reports)
                > self._TARGET_LOCK_REPORT_HISTORY_LIMIT
            ):
                self._submitted_target_lock_reports.popitem(last=False)
        self._async_runtime.submit(self._deliver_target_lock_report(report))
        self._log(
            "target_lock.report_accepted",
            "Orin kilitlenme raporu alındı; sunucu gönderimi kuyruğa alındı",
            "success",
            {
                "report_id": report.report_id,
                "mission_id": report.mission_id,
                "target_id": report.target_id,
                "autonomous": report.autonomous,
            },
        )
        return response

    async def _deliver_target_lock_report(self, report: TargetLockReport) -> None:
        try:
            ended_at = self._competition_event_datetime(report.ended_at_server)
            await asyncio.to_thread(
                self._competition.submit_lock_event,
                LockEvent(
                    ended_at=ended_at,
                    autonomous=report.autonomous,
                    vehicle_source="orin",
                ),
            )
        except Exception as exc:
            with self._target_lock_report_lock:
                current = self._submitted_target_lock_reports.get(report.report_id)
                if current is not None:
                    current = {
                        **current,
                        "execution": "accepted",
                        "delivery": "failed",
                    }
                    self._submitted_target_lock_reports[report.report_id] = current
            self._log(
                "target_lock.report_delivery_failed",
                "Kilitlenme raporu alındı ancak TEKNOFEST POST işlemi başarısız",
                "danger",
                {
                    "report_id": report.report_id,
                    "mission_id": report.mission_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return
        with self._target_lock_report_lock:
            current = self._submitted_target_lock_reports.get(report.report_id)
            if current is not None:
                self._submitted_target_lock_reports[report.report_id] = {
                    **current,
                    "execution": "submitted",
                    "delivery": "succeeded",
                }
        self._log(
            "target_lock.report_submitted",
            "Orin kilitlenme raporu TEKNOFEST sunucusuna gönderildi",
            "success",
            {
                "report_id": report.report_id,
                "mission_id": report.mission_id,
                "target_id": report.target_id,
                "autonomous": report.autonomous,
                "teknofest_payload": report.teknofest_payload(),
            },
        )

    def _expire_target_lock_status(self, generation: int) -> None:
        with self._lock:
            if generation != self._target_lock_status_generation:
                return
            self._target_lock_status_timer = None
        if self._telemetry is not None:
            self._telemetry.patch(target_track=TargetTrack())

    def _clear_target_lock_status(self) -> None:
        with self._lock:
            self._target_lock_status_generation += 1
            timer = self._target_lock_status_timer
            self._target_lock_status_timer = None
        if timer is not None:
            timer.cancel()
        if self._telemetry is not None:
            self._telemetry.patch(target_track=TargetTrack())

    def _submit_qr_report(self, message: OrinMessage) -> dict[str, Any]:
        report = KamikazeQrReport.from_payload(dict(message.payload))
        report_id = report.report_id
        with self._qr_report_lock:
            previous = self._submitted_qr_reports.get(report_id)
            if previous is not None:
                return dict(previous)

        payload = report.to_payload()
        event = OnboardMissionEvent(
            event=report.event,
            time_utc="",
            mission_id=report.mission_id,
            source="report",
            qr_text=report.qr_text,
            altitude_m=report.altitude_m,
            fields=payload,
        )
        self._process_event_payload(
            event.to_dict(),
            source="report",
            mission_id=event.mission_id,
        )
        response = {
            "accepted": True,
            "execution": "submitted",
            "report_id": report_id,
            "mission_id": event.mission_id,
        }
        with self._qr_report_lock:
            self._submitted_qr_reports[report_id] = dict(response)
            self._submitted_qr_reports.move_to_end(report_id)
            while len(self._submitted_qr_reports) > self._QR_REPORT_HISTORY_LIMIT:
                self._submitted_qr_reports.popitem(last=False)
        self._async_runtime.submit(self._deliver_qr_report(report))
        self._log(
            "kamikaze.qr_accepted",
            "Orin QR raporu alındı; Onboard zamanlarıyla gönderim kuyruğa alındı",
            "success",
            {
                "report_id": report_id,
                "mission_id": event.mission_id,
                "qr_text_length": len(report.qr_text),
            },
        )
        return response

    async def _deliver_qr_report(self, report: KamikazeQrReport) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self._QR_REPORT_DELIVERY_ATTEMPTS + 1):
            try:
                started_at = self._competition_event_datetime(
                    report.started_at_server
                )
                ended_at = self._competition_event_datetime(
                    report.ended_at_server
                )
                event = KamikazeEvent(
                    started_at=started_at,
                    ended_at=ended_at,
                    qr_text=report.qr_text,
                    vehicle_source="orin",
                )
                await asyncio.to_thread(
                    self._competition.submit_kamikaze_event,
                    event,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self._QR_REPORT_DELIVERY_ATTEMPTS:
                    break
                self._log(
                    "kamikaze.qr_delivery_retry",
                    "QR raporu TEKNOFEST gönderimi yeniden denenecek",
                    "warning",
                    {
                        "report_id": report.report_id,
                        "mission_id": report.mission_id,
                        "attempt": attempt,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(self._QR_REPORT_RETRY_DELAYS_S[attempt - 1])
                continue
            self._log(
                "kamikaze.qr_submitted",
                "Orin QR raporu Onboard başlangıç ve bitiş zamanlarıyla TEKNOFEST sunucusuna gönderildi",
                "success",
                {
                    "report_id": report.report_id,
                    "mission_id": report.mission_id,
                    "attempt": attempt,
                    "teknofest_payload": event.to_payload(),
                },
            )
            return
        assert last_error is not None
        self._log(
            "kamikaze.qr_delivery_failed",
            "QR raporu alındı ancak TEKNOFEST POST işlemi başarısız",
            "danger",
            {
                "report_id": report.report_id,
                "mission_id": report.mission_id,
                "attempts": self._QR_REPORT_DELIVERY_ATTEMPTS,
                "error": str(last_error),
                "error_type": type(last_error).__name__,
            },
        )

    def _handle_mission_event(self, message: OrinMessage) -> dict[str, Any]:
        self._process_event_payload(message.payload, source="live")
        return {}

    @staticmethod
    def _payload_datetime(
        payload: dict[str, Any],
        *keys: str,
        required: bool = True,
    ) -> datetime | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                if isinstance(value, (int, float)):
                    resolved = datetime.fromtimestamp(float(value) / 1_000.0, UTC)
                else:
                    resolved = datetime.fromisoformat(
                        str(value).strip().replace("Z", "+00:00")
                    )
                    if resolved.tzinfo is None:
                        raise ValueError("timezone is required")
                    resolved = resolved.astimezone(UTC)
            except (OSError, OverflowError, TypeError, ValueError) as exc:
                raise ValueError(f"Geçersiz QR zaman alanı: {key}") from exc
            return resolved
        if required:
            raise ValueError(f"QR zaman alanı zorunlu: {'/'.join(keys)}")
        return None

    def _kamikaze_report_datetime(
        self,
        payload: dict[str, Any],
        *,
        iso_keys: tuple[str, ...],
        server_keys: tuple[str, ...],
        required: bool = True,
    ) -> datetime | None:
        resolved = self._payload_datetime(payload, *iso_keys, required=False)
        if resolved is not None:
            return resolved

        raw: Mapping[str, Any] | None = None
        for key in server_keys:
            value = payload.get(key)
            if isinstance(value, Mapping):
                raw = value
                break
        if raw is None:
            if required:
                raise ValueError(
                    "QR zaman alanı zorunlu: " + "/".join((*iso_keys, *server_keys))
                )
            return None

        stamp = KamikazeServerTime.from_payload(raw)
        return self._competition_event_datetime(stamp)

    def _competition_event_datetime(
        self,
        stamp: CompetitionEventTime,
    ) -> datetime:
        estimate = getattr(self._competition, "estimated_server_time", None)
        reference = estimate() if callable(estimate) else None
        if not isinstance(reference, datetime) or reference.tzinfo is None:
            reference = datetime.now(UTC)
        reference = reference.astimezone(UTC)
        base = reference.replace(
            hour=stamp.hour,
            minute=stamp.minute,
            second=stamp.second,
            microsecond=stamp.millisecond * 1_000,
        )
        candidates = (base - timedelta(days=1), base, base + timedelta(days=1))
        return min(candidates, key=lambda value: abs((value - reference).total_seconds()))

    def _process_event_payload(
        self,
        payload: dict[str, Any],
        *,
        source: str,
        mission_id: int | None = None,
        sequence: int | None = None,
    ) -> None:
        normalized_payload = dict(payload)
        nested_fields = payload.get("fields")
        if isinstance(nested_fields, dict):
            for key, value in nested_fields.items():
                normalized_payload.setdefault(str(key), value)
        event_name = str(normalized_payload.get("event", "UNKNOWN")).strip().upper()
        resolved_mission_id = (
            mission_id
            or self._optional_int(normalized_payload.get("mission_id"))
            or self.state.mission_id
        )
        event_task_value = normalized_payload.get("task") or normalized_payload.get("mission")
        event_task = ""
        if event_task_value:
            event_task = self._canonical_task(str(event_task_value))
        if event_task and not self._matches_current_mission(
            event_task, resolved_mission_id
        ):
            self._log_ignored_mission_packet(
                "mission.event",
                event_task,
                resolved_mission_id,
            )
            return
        altitude = normalized_payload.get("altitude_m")
        event = OnboardMissionEvent(
            event=event_name,
            time_utc=str(
                normalized_payload.get("time_utc")
                or normalized_payload.get("timestamp")
                or ""
            ),
            mission_id=(
                resolved_mission_id
            ),
            sequence=sequence,
            source=source,
            qr_text=(
                str(normalized_payload["qr_text"])
                if normalized_payload.get("qr_text") is not None
                else None
            ),
            altitude_m=(float(altitude) if altitude is not None else None),
            fields=normalized_payload,
        )
        with self._lock:
            if event.deduplication_key in self._event_keys:
                return
            self._event_keys.add(event.deduplication_key)
            timeline = (*self._state.timeline, event)[-self._TIMELINE_LIMIT :]
            changes: dict[str, Any] = {
                "timeline": timeline,
                "critical_event": (
                    event_name
                    if event_name in CRITICAL_KAMIKAZE_EVENTS
                    else self._state.critical_event
                ),
                "qr_status": (
                    "cancelled"
                    if event_name == "SAFETY_FLOOR_BREACH"
                    and bool(normalized_payload.get("qr_cancelled"))
                    and self._state.qr_text is None
                    else self._state.qr_status
                ),
                "waiting_for_guided": (
                    True
                    if event_name == "WAITING_FOR_GUIDED"
                    else False
                    if event_name == "GUIDED_READY"
                    else self._state.waiting_for_guided
                ),
            }
            if event_name in ACTUATOR_TEST_EVENTS:
                servo_output = self._integer_or_none(
                    normalized_payload.get("servo_output")
                )
                pwm = self._integer_or_none(normalized_payload.get("pwm"))
                armed = self._boolean_or_none(normalized_payload.get("armed"))
                changes.update(
                    {
                        "active_task": ACTUATOR_TEST_TASK,
                        "mission_id": event.mission_id or self._state.mission_id,
                        "actuator_step": str(
                            normalized_payload.get("step")
                            or event_name.rsplit(".", 1)[-1].lower()
                        ),
                        "actuator_step_count": (
                            self._state.actuator_step_count + 1
                            if event_name == "ACTUATOR_TEST.STEP"
                            else self._state.actuator_step_count
                        ),
                        "actuator_servo_output": (
                            servo_output
                            if servo_output is not None
                            else self._state.actuator_servo_output
                        ),
                        "actuator_pwm": (
                            pwm if pwm is not None else self._state.actuator_pwm
                        ),
                        "actuator_armed": (
                            armed if armed is not None else self._state.actuator_armed
                        ),
                        "actuator_notification_only": (
                            self._state.actuator_notification_only
                            or event_name == "ACTUATOR_TEST.NOTIFICATION_ONLY"
                            or servo_output == 0
                        ),
                    }
                )
            self._state = replace(self._state, **changes)
        if event_name in QR_EVENT_TYPES and event.qr_text:
            self._accept_qr(event)
        actuator_event = event_name in ACTUATOR_TEST_EVENTS
        severity = (
            "danger"
            if event.critical
            else "success"
            if event_name in QR_EVENT_TYPES
            or event_name in {
                "ACTUATOR_TEST.NOTIFICATION_ONLY",
                "ACTUATOR_TEST.COMPLETED",
            }
            else "warning"
            if event_name in {"ABORT", "FAULT"}
            else "info"
        )
        self._log(
            "actuator_test.event" if actuator_event else "kamikaze.event",
            (
                f"Aktüatör testi olayı: {event_name}"
                if actuator_event
                else f"Kamikaze olayı: {event_name}"
            ),
            severity,
            {
                **normalized_payload,
                "mission_id": event.mission_id,
                "source": source,
            },
        )
        self._events.publish(event)
        self._publish()

    def _accept_qr(self, event: OnboardMissionEvent) -> None:
        assert event.qr_text is not None
        with self._lock:
            current = self._state.qr_text
            conflicts = list(self._state.qr_conflicts)
            if current is not None and current != event.qr_text:
                if event.qr_text not in conflicts:
                    conflicts.append(event.qr_text)
                status = "conflict"
            else:
                status = "decoded"
            self._state = replace(
                self._state,
                qr_status=status,
                qr_text=current or event.qr_text,
                qr_event=event.event,
                qr_time_utc=event.time_utc,
                qr_conflicts=tuple(conflicts),
            )
        self._log(
            "kamikaze.qr_received" if status == "decoded" else "kamikaze.qr_conflict",
            "Kamikaze QR sonucu alındı" if status == "decoded" else "Kamikaze QR kaynakları uyuşmuyor",
            "success" if status == "decoded" else "warning",
            {"mission_id": event.mission_id, "event": event.event, "qr_text": event.qr_text, "source": event.source},
        )

    def _on_orin_changed(self, state: OrinConnectionState) -> None:
        recover_finished_mission = False
        with self._lock:
            was_connected = self._state.connected
            session_changed = bool(
                state.alive
                and state.session_id
                and state.session_id != self._last_orin_session
            )
            if state.session_id:
                self._last_orin_session = state.session_id
            target_ready = self._state.target_ready
            target_revision = self._state.target_revision
            target = dict(self._state.target)
            hss_ready = self._state.hss_ready
            if state.alive and (not was_connected or session_changed):
                target_ready = False
                target_revision = 0
                target = {}
                hss_ready = False
                self._target_acknowledged_hash = ""
                self._hss_acknowledged_hash = ""
            elif state.alive and not state.kamikaze_target_ready:
                target_ready = False
                self._target_acknowledged_hash = ""
            advertised_tasks = self._normalized_tasks(
                (
                    *state.available_tasks,
                    *(
                        capability
                        for capability in state.capabilities
                        if str(capability).strip().lower() == ACTUATOR_TEST_TASK
                    ),
                )
            )
            available_tasks = tuple(
                dict.fromkeys((*self._state.available_tasks, *advertised_tasks))
            )
            remote_active_tasks = self._normalized_tasks(state.active_tasks)
            advertised_active_task = (
                self._canonical_task(state.active_task)
                if state.active_task
                else ""
            )
            preserve_foreground = bool(
                self._state.active_task
                and not self._state.terminal
                and (
                    self._state.active_task in remote_active_tasks
                    or self._state.mission_id in state.active_missions
                    or self._state.active_task == "target.lock"
                    and self._state.target_lock_phase not in TERMINAL_MISSION_PHASES
                )
            )
            active_task = (
                self._state.active_task
                if preserve_foreground
                else advertised_active_task or self._state.active_task
            )
            mission_id = (
                state.active_missions[0]
                if len(state.active_missions) == 1
                else self._state.mission_id
            )
            phase = self._state.phase
            waiting_for_guided = self._state.waiting_for_guided
            if session_changed:
                active_task = ""
                mission_id = None
                phase = "idle"
                waiting_for_guided = False
                self._remote_active_observed = False
            elif (
                self._state.mission_id is not None
                and (
                    self._state.mission_id in state.active_missions
                    or self._state.active_task in remote_active_tasks
                    or (
                        bool(state.active_task)
                        and self._state.active_task == advertised_active_task
                    )
                )
            ):
                self._remote_active_observed = True
            elif (
                self._remote_active_observed
                and not self._state.terminal
                and not state.active_missions
                and not state.active_tasks
                and not state.active_task
            ):
                self._remote_active_observed = False
                recover_finished_mission = True
            self._state = replace(
                self._state,
                connected=state.alive,
                onboard_version=state.version,
                vehicle_mode=state.vehicle_mode,
                active_task=active_task,
                available_tasks=available_tasks,
                queued_tasks=tuple(state.queued_tasks),
                mission_id=mission_id,
                phase=phase,
                waiting_for_guided=waiting_for_guided,
                pending_command=("" if session_changed else self._state.pending_command),
                target_ready=target_ready,
                target_revision=(
                    state.kamikaze_target_revision
                    if state.kamikaze_target_ready
                    else target_revision
                ),
                target=target,
                hss_ready=hss_ready,
                last_error=state.last_error if not state.alive else self._state.last_error,
            )
        self._publish()
        if recover_finished_mission:
            self._schedule_recovery(0.1)
        if state.alive and (not was_connected or session_changed):
            self._start_recovery()

    def _start_recovery(self) -> None:
        with self._lock:
            if self._recovery_running or not self._started or not self._orin.connected:
                return
            self._recovery_running = True
        threading.Thread(target=self._recover, name="OnboardMissionRecovery", daemon=True).start()

    def _schedule_recovery(self, delay: float) -> None:
        def run() -> None:
            with self._lock:
                self._recovery_timer = None
            self._start_recovery()

        with self._lock:
            if not self._started:
                return
            previous = self._recovery_timer
            timer = threading.Timer(max(0.0, float(delay)), run)
            timer.daemon = True
            self._recovery_timer = timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _recover(self) -> None:
        reconciled = False
        try:
            receipt = self._send_request(MissionList())
            if receipt.accepted:
                self._apply_mission_list(receipt.response)
                reconciled = True
            mission_ids = set(self._event_cursors)
            if self.state.mission_id is not None:
                mission_ids.add(self.state.mission_id)
            for mission_id in mission_ids:
                after_sequence = self._event_cursors.get(mission_id, 0)
                response = self._send_request(
                    MissionEventsQuery(
                        mission_ids=(mission_id,),
                        event_types=("log", "transition", "error"),
                        minimum_level="DEBUG",
                        after_sequence=after_sequence,
                        limit=200,
                    )
                )
                if response.accepted:
                    self._apply_recovered_events(mission_id, response.response.get("events", []))
        except Exception as exc:
            self._log(
                "mission.recovery_failed",
                "Onboard görev geçmişi uzlaştırılamadı",
                "warning",
                {"error": str(exc), "error_type": type(exc).__name__},
            )
        finally:
            with self._lock:
                self._recovery_running = False
                if not reconciled and self._state.phase == "reconciling":
                    self._state = replace(self._state, phase="failed")
            self._publish()

    def _apply_mission_list(self, response: Any) -> None:
        if isinstance(response, dict):
            missions = response.get("missions", [])
            advertised = response.get("available_tasks", response.get("tasks", []))
        else:
            missions = response
            advertised = []
        if not isinstance(missions, list):
            return
        listed_tasks = [
            str(item.get("task", ""))
            for item in missions
            if isinstance(item, dict) and str(item.get("task", "")).strip()
        ]
        available_tasks = self._normalized_tasks(
            (
                *self.state.available_tasks,
                *(advertised if isinstance(advertised, (list, tuple)) else ()),
                *listed_tasks,
            )
        )
        current = self.state
        active_items = [
            item for item in missions
            if isinstance(item, dict)
            and str(item.get("task", "")).strip()
            and str(item.get("phase", "")).strip().lower()
            in {"queued", "starting", "running", "pausing", "paused", "stopping"}
        ]
        active = next(
            (
                item for item in active_items
                if self._canonical_task(str(item.get("task", ""))) == current.active_task
                and (
                    current.mission_id is None
                    or self._optional_int(item.get("mission_id")) == current.mission_id
                )
            ),
            None,
        )
        if active is None:
            active = next(
                (
                    item for item in active_items
                    if self._canonical_task(str(item.get("task", ""))) not in self._HSS_TASKS
                ),
                active_items[0] if active_items else None,
            )
        terminal = None
        if active is None and (current.active_task or current.mission_id is not None):
            candidates = [
                item
                for item in missions
                if isinstance(item, dict)
                and str(item.get("task", "")).strip()
                and str(item.get("phase", "")).strip().lower()
                in {"stopped", "succeeded", "failed", "cancelled"}
                and (
                    self._optional_int(item.get("mission_id")) == current.mission_id
                    or self._canonical_task(str(item.get("task", "")))
                    == current.active_task
                )
            ]
            if candidates:
                terminal = max(
                    candidates,
                    key=lambda item: float(item.get("updated_at") or 0.0),
                )
        with self._lock:
            last_error = self._state.last_error
            contexts = {
                key: dict(value) for key, value in self._state.mission_contexts.items()
            }
            target_lock_mission_id = self._state.target_lock_mission_id
            target_lock_phase = self._state.target_lock_phase
            for item in missions:
                if not isinstance(item, dict) or not str(item.get("task", "")).strip():
                    continue
                item_task = self._canonical_task(str(item.get("task", "")))
                item_mission_id = self._optional_int(item.get("mission_id"))
                item_phase = str(item.get("phase", "unknown")).strip().lower()
                if item_mission_id is not None:
                    contexts[f"{item_task}:{item_mission_id}"] = {
                        "task": item_task,
                        "mission_id": item_mission_id,
                        "phase": item_phase,
                    }
                    if item_task == "target.lock":
                        target_lock_mission_id = item_mission_id
                        target_lock_phase = item_phase
            if active is not None:
                active_task = self._canonical_task(str(active.get("task", "")))
                mission_id = self._optional_int(active.get("mission_id"))
                phase = str(active.get("phase", self._state.phase)).lower()
                result = self._state.result
                result_received = self._state.result_received
            elif terminal is not None:
                active_task = self._canonical_task(str(terminal.get("task", "")))
                mission_id = self._optional_int(terminal.get("mission_id"))
                phase = str(terminal.get("phase", "failed")).lower()
                result = dict(terminal)
                result_received = True
                last_error = (
                    str(terminal.get("reason") or "")
                    if phase == "failed"
                    else ""
                )
            elif not self._state.terminal:
                active_task = ""
                mission_id = None
                phase = "idle"
                result = self._state.result
                result_received = self._state.result_received
            else:
                active_task = self._state.active_task
                mission_id = self._state.mission_id
                phase = self._state.phase
                result = self._state.result
                result_received = self._state.result_received
                last_error = self._state.last_error
            self._state = replace(
                self._state,
                available_tasks=available_tasks,
                mission_contexts=contexts,
                target_lock_mission_id=target_lock_mission_id,
                target_lock_phase=target_lock_phase,
                active_task=active_task,
                mission_id=mission_id,
                phase=phase,
                waiting_for_guided=(
                    self._state.waiting_for_guided if active is not None else False
                ),
                result=result,
                result_received=result_received,
                last_error=last_error,
            )
        self._publish()

    def _apply_recovered_events(self, mission_id: int, events: Any) -> None:
        if not isinstance(events, list):
            return
        for raw in events:
            if not isinstance(raw, dict):
                continue
            sequence = self._optional_int(raw.get("sequence")) or 0
            self._event_cursors[mission_id] = max(self._event_cursors.get(mission_id, 0), sequence)
            fields: dict[str, Any] = (
                dict(raw["fields"])
                if isinstance(raw.get("fields"), dict)
                else {}
            )
            event_name = str(fields.get("event", "")).upper()
            if event_name in QR_EVENT_TYPES or event_name in ACTUATOR_TEST_EVENTS:
                self._process_event_payload(
                    dict(fields),
                    source="recovery",
                    mission_id=mission_id,
                    sequence=sequence,
                )

    def _set_error(self, message: str, *, phase: str | None = None) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                last_error=message,
                phase=phase or self._state.phase,
            )
        self._publish()

    def _publish(self) -> None:
        self._changed.publish(self.state)

    def _log(
        self,
        action: str,
        message: str,
        severity: str,
        details: dict[str, Any],
    ) -> None:
        if self._logger is not None:
            self._logger.event(action, message, severity=severity, details=details)

    @staticmethod
    def _canonical_task(task: str) -> str:
        try:
            normalized = str(task).strip().upper()
            if normalized == "KAMIKAZE_CHECK":
                return KAMIKAZE_CHECK_TASK
            return contract_canonical_task(task)
        except ValueError as exc:
            raise ValueError("Görev adı boş olamaz") from exc

    def _matches_current_mission(self, task: str, mission_id: int | None) -> bool:
        current = self.state
        if mission_id is None:
            if current.mission_id is not None:
                return False
            if not task or not current.active_task:
                return True
            return current.active_task == self._canonical_task(task)
        if not self._valid_mission_identity(task, mission_id):
            return False
        canonical = self._canonical_task(task)
        if current.active_task == canonical and (
            current.mission_id is None or current.mission_id == mission_id
        ):
            return True
        return False

    def _valid_mission_identity(self, task: str, mission_id: int | None) -> bool:
        if not task or mission_id is None or mission_id <= 0:
            return False
        try:
            return bool(self._canonical_task(task))
        except ValueError:
            return False

    def _log_ignored_mission_packet(
        self,
        packet_type: str,
        task: str,
        mission_id: int | None,
    ) -> None:
        current = self.state
        self._log(
            "mission.packet_ignored",
            "Başka bir göreve ait onboard paketi aktif durumu değiştirmedi",
            "warning",
            {
                "packet_type": packet_type,
                "task": task,
                "mission_id": mission_id,
                "active_task": current.active_task,
                "active_mission_id": current.mission_id,
            },
        )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _normalized_tasks(cls, tasks: Any) -> tuple[str, ...]:
        if not isinstance(tasks, (list, tuple)):
            return ()
        result: list[str] = []
        for task in tasks:
            try:
                canonical = cls._canonical_task(str(task))
            except ValueError:
                continue
            if canonical not in result:
                result.append(canonical)
        return tuple(result)

    @staticmethod
    def _integer_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _boolean_or_none(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "yes", "1", "armed"}:
            return True
        if normalized in {"false", "no", "0", "disarmed"}:
            return False
        return None


__all__ = ["OnboardMissionService"]
