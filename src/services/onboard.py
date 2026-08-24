from __future__ import annotations

import asyncio
import concurrent.futures
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
    ContractModel,
    HssUpdate as ContractHssUpdate,
    HssZone as ContractHssZone,
    HssWaypoint,
    KamikazeApproachPlanUpdate,
    KamikazeServerTime,
    KamikazeTargetUpdate,
    MissionEventsQuery,
    MissionList,
    MissionQuery,
    PacketType,
    TaskCancel,
    TaskPause,
    TaskResume,
    TaskStart,
    TaskStop,
    canonical_task as contract_canonical_task,
)
from src.core.events import EventBus, Subscription
from src.models.competition import HssZone, KamikazeEvent, QrCoordinate
from src.models.geometry import Point
from src.models.kamikaze_approach import KamikazeApproachPlan
from src.models.onboard import (
    ACTUATOR_TEST_EVENTS,
    ACTUATOR_TEST_TASK,
    CRITICAL_KAMIKAZE_EVENTS,
    QR_EVENT_TYPES,
    OnboardMissionEvent,
    OnboardMissionState,
)
from src.models.orin import (
    OrinCommandReceipt,
    OrinConnectionState,
    OrinHandlerResponse,
    OrinMessage,
)
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.onboard import OnboardMissionServiceContract
from src.services.abstracts.orin import OrinService
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

    def __init__(
        self,
        orin: OrinService,
        competition: CompetitionService,
        logger: ServiceLogger | None = None,
        *,
        async_runtime: AsyncRuntime | None = None,
    ) -> None:
        self._orin = orin
        self._competition = competition
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
        self._reference_sync_future: concurrent.futures.Future[Any] | None = None
        self._reference_sync_generation = 0
        self._reference_sync_processed = 0
        self._hss_observed = False
        self._hss_revision = 0
        self._hss_content_hash = ""
        self._hss_acknowledged_hash = ""
        self._hss_snapshot: tuple[HssZone, ...] = ()
        self._target_source_revision = 0
        self._target_content_hash = ""
        self._target_acknowledged_hash = ""
        self._last_orin_session = ""
        self._submitted_qr_reports: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._target_sync_lock = threading.RLock()
        self._hss_sync_lock = threading.RLock()
        self._qr_report_lock = threading.RLock()

    @property
    def state(self) -> OnboardMissionState:
        with self._lock:
            return replace(
                self._state,
                result=dict(self._state.result),
                target=dict(self._state.target),
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
                self._competition.hss_updated.subscribe(self._on_hss_updated),
                self._competition.qr_updated.subscribe(self._on_qr_updated),
            ]
        )
        if self._competition.hss_zones:
            self._on_hss_updated(self._competition.hss_zones)
        if self._competition.qr_coordinate is not None:
            self._on_qr_updated(self._competition.qr_coordinate)
        self._on_orin_changed(self._orin.state)

    def stop(self) -> None:
        with self._lock:
            self._started = False
            recovery_timer = self._recovery_timer
            self._recovery_timer = None
        if recovery_timer is not None:
            recovery_timer.cancel()
        future = self._reference_sync_future
        self._reference_sync_future = None
        if future is not None and not future.done():
            future.cancel()
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
            if (
                self._state.active_task == canonical
                and not self._state.terminal
            ):
                raise RuntimeError(f"Onboard görevi zaten çalışıyor: {canonical}")
        if canonical == "kamikaze.execute":
            self._sync_kamikaze_target()
        wire_source = "ui" if source in {"ui", "qt-ui", "operator-ui"} else source
        return self._issue(
            TaskStart(canonical, wire_source),
            pending="start",
            task=canonical,
        )

    def start_kamikaze_transit(
        self,
        plan: KamikazeApproachPlan,
        *,
        source: str = "ui",
    ) -> OrinCommandReceipt:
        """Atomically publish the locked approach plan, then launch onboard transit.

        Target and HSS references are synchronized first so the approach contract
        carries the exact revisions that onboard will validate.  The selected GCS
        geometry is never silently rebuilt here.
        """
        selected = plan.selected_corridor
        if selected is None or not plan.locked:
            raise RuntimeError("Kamikaze transit için önce Top-5 içinden koridor kilitlenmeli")
        with self._lock:
            if self._state.active_task == "kamikaze.transit" and not self._state.terminal:
                raise RuntimeError("Onboard kamikaze.transit görevi zaten çalışıyor")

        # Refresh target, but never move a locked corridor silently.
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

        # Force an explicit current HSS snapshot, including the zero-zone case.
        self._on_hss_updated(tuple(self._competition.hss_zones))
        self._send_hss_update()
        with self._lock:
            target_revision = self._target_source_revision
            hss_revision = self._hss_revision

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
            hss_revision=int(hss_revision),
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
        return self._issue(
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

    def _sync_kamikaze_target(self) -> OrinCommandReceipt | None:
        """Forward the latest TEKNOFEST QR target before a kamikaze launch."""

        try:
            self._competition.refresh_qr_coordinate()
            target = self._competition.qr_coordinate
            if target is None:
                raise RuntimeError("TEKNOFEST sunucusundan QR hedef koordinatı alınamadı")
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
        self._schedule_reference_sync()

    def _on_qr_updated(self, target: QrCoordinate | None) -> None:
        if target is not None:
            self._schedule_reference_sync()

    def _schedule_reference_sync(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._reference_sync_generation += 1
            future = self._reference_sync_future
            if future is not None and not future.done():
                return
            future = self._async_runtime.submit(self._reference_sync_loop())
            self._reference_sync_future = future
        future.add_done_callback(self._reference_sync_done)

    def _reference_sync_done(self, future: concurrent.futures.Future[Any]) -> None:
        failed = False
        if not future.cancelled():
            try:
                future.result()
            except Exception as exc:
                failed = True
                self._log(
                    "competition.reference_sync_failed",
                    "Onboard referans senkronizasyonu beklenmedik şekilde durdu",
                    "warning",
                    {"error": str(exc), "error_type": type(exc).__name__},
                )
        with self._lock:
            if self._reference_sync_future is not future:
                return
            self._reference_sync_future = None
            reschedule = (
                not failed
                and self._started
                and self._reference_sync_processed < self._reference_sync_generation
            )
        if reschedule:
            with self._lock:
                if not self._started or self._reference_sync_future is not None:
                    return
                next_future = self._async_runtime.submit(
                    self._reference_sync_loop()
                )
                self._reference_sync_future = next_future
            next_future.add_done_callback(self._reference_sync_done)

    async def _reference_sync_loop(self) -> None:
        while True:
            with self._lock:
                generation = self._reference_sync_generation
            await asyncio.to_thread(self._sync_reference_once)
            with self._lock:
                self._reference_sync_processed = generation
                if generation == self._reference_sync_generation:
                    return

    def _sync_reference_once(self) -> None:
        if not self.state.connected:
            return
        with self._lock:
            hss_observed = self._hss_observed
        if hss_observed:
            try:
                self._send_hss_update()
            except Exception as exc:
                self._log(
                    "competition.hss_sync_failed",
                    "HSS durumu Orin'e aktarılamadı",
                    "warning",
                    {"error": str(exc), "error_type": type(exc).__name__},
                )
        target = self._competition.qr_coordinate
        if target is not None:
            try:
                self._send_target_update(target)
            except Exception as exc:
                self._record_target_failure(exc)

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
            return self._task_control(TaskCancel(canonical), canonical, "cancel")
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
            return OrinCommandReceipt(
                request_id,
                True,
                message,
                response,
            )

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
            starting_kamikaze = starting and task in {"kamikaze.execute", "kamikaze.transit"}
            starting_actuator_test = starting and task == ACTUATOR_TEST_TASK
            self._state = replace(
                self._state,
                pending_command=pending,
                last_error="",
                active_task=task or self._state.active_task,
                mission_id=(None if starting else self._state.mission_id),
                phase=("requesting" if starting else self._state.phase),
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
            self._state = replace(
                self._state,
                active_task=(
                    self._canonical_task(acknowledged_task)
                    if acknowledged_task
                    else self._state.active_task
                ),
                mission_id=(None if already_inactive else mission_id or self._state.mission_id),
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

    def _handle_transition(self, message: OrinMessage) -> dict[str, Any]:
        payload = message.payload
        current = str(payload.get("current", "unknown")).strip().lower()
        mission_id = self._optional_int(payload.get("mission_id"))
        task_value = str(payload.get("task") or self.state.active_task).strip()
        task = self._canonical_task(task_value) if task_value else ""
        reason = str(payload.get("reason", "")).strip()
        if not self._matches_current_mission(task, mission_id):
            self._log_ignored_mission_packet("mission.transition", task, mission_id)
            return {}
        with self._lock:
            self._state = replace(
                self._state,
                active_task=task or self._state.active_task,
                mission_id=mission_id or self._state.mission_id,
                phase=current,
                waiting_for_guided=(current == "starting" and self._state.waiting_for_guided),
                last_error=reason if current == "failed" else self._state.last_error,
            )
        self._log(
            "mission.transition",
            f"Onboard görev geçişi: {payload.get('previous', '-')} → {current}",
            "danger" if current == "failed" else "success" if current == "succeeded" else "info",
            dict(payload),
        )
        self._publish()
        return {}

    def _handle_result(self, message: OrinMessage) -> dict[str, Any]:
        payload = message.payload
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
        if not result and isinstance(payload.get("result"), dict):
            result = dict(payload["result"])
        execution = str(payload.get("execution") or snapshot.get("phase") or "failed").lower()
        mission_id = self._optional_int(payload.get("mission_id") or snapshot.get("mission_id"))
        task_value = str(
            payload.get("task") or snapshot.get("task") or self.state.active_task
        ).strip()
        task = self._canonical_task(task_value) if task_value else ""
        if not self._matches_current_mission(task, mission_id):
            self._log_ignored_mission_packet("mission.result", task, mission_id)
            return {}
        with self._lock:
            self._state = replace(
                self._state,
                active_task=task or self._state.active_task,
                mission_id=mission_id or self._state.mission_id,
                phase=execution,
                waiting_for_guided=False,
                result=(dict(snapshot) if snapshot else dict(payload)),
                result_received=True,
                last_error=(
                    str(snapshot.get("reason") or payload.get("reason") or "")
                    if execution == "failed"
                    else self._state.last_error
                ),
            )
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
        self._log(
            "mission.result",
            f"Onboard görev sonucu: {execution}",
            "success" if execution == "succeeded" else "warning" if execution == "cancelled" else "danger",
            {"mission_id": mission_id, "task": self.state.active_task, "snapshot": dict(snapshot)},
        )
        self._publish()
        return {}

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

    def _submit_qr_report(self, message: OrinMessage) -> dict[str, Any]:
        payload = dict(message.payload)
        report_id = str(payload.get("report_id", "")).strip()
        if not report_id:
            raise ValueError("QR report_id alanı zorunlu")
        if len(report_id) > 192:
            raise ValueError("QR report_id alanı 192 karakteri aşamaz")
        with self._lock:
            previous = self._submitted_qr_reports.get(report_id)
            if previous is not None:
                return dict(previous)

        qr_text = str(payload.get("qr_text", "")).strip()
        if not qr_text:
            raise ValueError("QR raporu boş qr_text içeremez")
        if len(qr_text) > 4096:
            raise ValueError("QR raporu 4096 karakteri aşamaz")
        ended_at = self._kamikaze_report_datetime(
            payload,
            iso_keys=("ended_at_utc", "observed_at_utc", "time_utc"),
            server_keys=("kamikazeBitisZamani", "ended_at_server"),
        )
        started_at = self._kamikaze_report_datetime(
            payload,
            iso_keys=("started_at_utc",),
            server_keys=("kamikazeBaslangicZamani", "started_at_server"),
            required=False,
        ) or ended_at
        mission_id = self._optional_int(payload.get("mission_id"))
        if mission_id is None or mission_id <= 0:
            raise ValueError("QR mission_id alanı pozitif olmalı")
        altitude = payload.get("altitude_m")
        event = OnboardMissionEvent(
            event=str(payload.get("event") or "QR_DECODED"),
            time_utc=ended_at.isoformat().replace("+00:00", "Z"),
            mission_id=mission_id,
            source="report",
            qr_text=qr_text,
            altitude_m=(float(altitude) if altitude is not None else None),
            fields={**payload, "report_id": report_id},
        )
        self._process_event_payload(
            event.to_dict(),
            source="report",
            mission_id=event.mission_id,
        )
        self._competition.submit_kamikaze_event(
            KamikazeEvent(
                started_at=started_at,
                ended_at=ended_at,
                qr_text=qr_text,
                vehicle_source="orin",
            )
        )
        response = {
            "execution": "submitted",
            "report_id": report_id,
            "mission_id": event.mission_id,
        }
        with self._lock:
            self._submitted_qr_reports[report_id] = dict(response)
            self._submitted_qr_reports.move_to_end(report_id)
            while len(self._submitted_qr_reports) > self._QR_REPORT_HISTORY_LIMIT:
                self._submitted_qr_reports.popitem(last=False)
        self._log(
            "kamikaze.qr_submitted",
            "Orin QR raporu TEKNOFEST sunucusuna gönderildi",
            "success",
            {
                "report_id": report_id,
                "mission_id": event.mission_id,
                "qr_text_length": len(qr_text),
            },
        )
        return response

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
        if not self._matches_current_mission(event_task, resolved_mission_id):
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
            active_task = (
                self._canonical_task(state.active_task)
                if state.active_task
                else self._state.active_task
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
                        and self._state.active_task == active_task
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
            self._schedule_reference_sync()
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
        active = next(
            (
                item for item in missions
                if isinstance(item, dict)
                and str(item.get("task", "")).strip()
                and str(item.get("phase", "")).strip().lower()
                in {
                    "queued",
                    "starting",
                    "running",
                    "pausing",
                    "paused",
                    "stopping",
                }
            ),
            None,
        )
        current = self.state
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
            if active is not None:
                last_error = self._state.last_error
            elif terminal is None and not self._state.terminal:
                last_error = self._state.last_error
            self._state = replace(
                self._state,
                available_tasks=available_tasks,
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
            fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
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
            return contract_canonical_task(task)
        except ValueError as exc:
            raise ValueError("Görev adı boş olamaz") from exc

    def _matches_current_mission(self, task: str, mission_id: int | None) -> bool:
        current = self.state
        if (
            current.mission_id is not None
            and mission_id is not None
            and current.mission_id != mission_id
        ):
            return False
        if current.active_task and task:
            try:
                return current.active_task == self._canonical_task(task)
            except ValueError:
                return False
        return True

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
