from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from src.core.events import EventBus, Subscription
from src.models.onboard import (
    CRITICAL_KAMIKAZE_EVENTS,
    QR_EVENT_TYPES,
    OnboardMissionEvent,
    OnboardMissionState,
)
from src.models.orin import OrinCommandReceipt, OrinConnectionState, OrinMessage
from src.services.contracts.competition import CompetitionService
from src.services.contracts.onboard import OnboardMissionServiceContract
from src.services.contracts.orin import OrinService
from src.services.logging_service import ServiceLogger


TASK_ALIASES = {
    "KAMIKAZE": "kamikaze.execute",
}


class OnboardCommandRejectedError(RuntimeError):
    def __init__(self, message: str, response: dict[str, Any]) -> None:
        super().__init__(message)
        self.response = dict(response)


class OnboardMissionService(OnboardMissionServiceContract):
    """Correlate onboard commands, mission events and recoverable QR state."""

    _TIMELINE_LIMIT = 200

    def __init__(
        self,
        orin: OrinService,
        competition: CompetitionService,
        logger: ServiceLogger | None = None,
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
                self._orin.register_handler("mission.transition", self._handle_transition),
                self._orin.register_handler("mission.result", self._handle_result),
                self._orin.register_handler("mission.chain", self._handle_chain),
                self._orin.register_handler("kamikaze.event", self._handle_kamikaze_event),
            ]
        )
        self._on_orin_changed(self._orin.state)

    def stop(self) -> None:
        with self._lock:
            self._started = False
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()

    def start_task(self, task: str, *, source: str = "ui") -> OrinCommandReceipt:
        canonical = self._canonical_task(task)
        if canonical == "kamikaze.execute":
            self._sync_kamikaze_target()
        return self._issue(
            "task.start",
            {"task": "KAMIKAZE" if canonical == "kamikaze.execute" else canonical, "source": source},
            pending="start",
            task=canonical,
        )

    def _sync_kamikaze_target(self) -> OrinCommandReceipt:
        """Forward the latest TEKNOFEST QR target before a kamikaze launch."""

        try:
            self._competition.refresh_qr_coordinate()
            target = self._competition.qr_coordinate
            if target is None:
                raise RuntimeError("TEKNOFEST sunucusundan QR hedef koordinatı alınamadı")
            payload = {
                "latitude_deg": float(target.latitude),
                "longitude_deg": float(target.longitude),
                "source": "teknofest",
                "fetched_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            self._log(
                "kamikaze.target_tx",
                "TEKNOFEST QR hedefi Orin'e gönderiliyor",
                "info",
                dict(payload),
            )
            receipt = self._orin.issue("kamikaze.target.update", payload)
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
            if not isinstance(accepted_target, dict):
                raise OnboardCommandRejectedError(
                    "Orin ACK yanıtında kabul edilen kamikaze hedefi bulunmuyor",
                    receipt.response,
                )
            revision = self._optional_int(receipt.response.get("target_revision"))
            if revision is None:
                raise OnboardCommandRejectedError(
                    "Orin ACK yanıtında geçerli hedef revizyonu bulunmuyor",
                    receipt.response,
                )
            with self._lock:
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
                },
            )
            self._publish()
            return receipt
        except Exception as exc:
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
            raise

    def pause_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control("task.pause", task, "pause")

    def resume_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control("task.resume", task, "resume")

    def stop_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control("task.stop", task, "stop")

    def cancel_task(self, task: str = "kamikaze.execute") -> OrinCommandReceipt:
        return self._task_control("task.cancel", task, "cancel")

    def refresh(self) -> OrinCommandReceipt:
        receipt = self._issue("mission.list", {}, pending="refresh")
        self._apply_mission_list(receipt.response.get("missions", []))
        return receipt

    def mission_status(
        self,
        *,
        mission_id: int | None = None,
        task: str | None = None,
    ) -> OrinCommandReceipt:
        if mission_id is None and not task:
            raise ValueError("Mission ID veya görev adı gerekli")
        payload = (
            {"mission": int(mission_id)}
            if mission_id is not None
            else {"task": self._canonical_task(str(task))}
        )
        receipt = self._issue("mission.status", payload, pending="status")
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
            "mission.events",
            {
                "mission": int(mission_id),
                "minimum_level": "DEBUG",
                "after_sequence": cursor,
                "limit": int(limit),
            },
            pending="events",
        )
        self._apply_recovered_events(int(mission_id), receipt.response.get("events", []))
        return receipt

    def start_chain(self, chain: str) -> OrinCommandReceipt:
        chain_id = str(chain).strip()
        if not chain_id:
            raise ValueError("Chain kimliği boş olamaz")
        return self._issue("chain.start", {"chain": chain_id}, pending="chain")

    def _task_control(self, packet_type: str, task: str, pending: str) -> OrinCommandReceipt:
        return self._issue(
            packet_type,
            {"task": self._canonical_task(task)},
            pending=pending,
            task=self._canonical_task(task),
        )

    def _issue(
        self,
        packet_type: str,
        payload: dict[str, Any],
        *,
        pending: str,
        task: str = "",
    ) -> OrinCommandReceipt:
        with self._lock:
            if self._state.pending_command:
                raise RuntimeError(f"Onboard komutu zaten bekleniyor: {self._state.pending_command}")
            self._state = replace(
                self._state,
                pending_command=pending,
                last_error="",
                active_task=task or self._state.active_task,
                mission_id=(None if packet_type == "task.start" else self._state.mission_id),
                phase=("requesting" if packet_type == "task.start" else self._state.phase),
                qr_status=("pending" if packet_type == "task.start" else self._state.qr_status),
                qr_text=(None if packet_type == "task.start" else self._state.qr_text),
                qr_event=("" if packet_type == "task.start" else self._state.qr_event),
                qr_time_utc=("" if packet_type == "task.start" else self._state.qr_time_utc),
                qr_conflicts=(() if packet_type == "task.start" else self._state.qr_conflicts),
                critical_event=("" if packet_type == "task.start" else self._state.critical_event),
                result=({} if packet_type == "task.start" else self._state.result),
                result_received=(False if packet_type == "task.start" else self._state.result_received),
                timeline=(() if packet_type == "task.start" else self._state.timeline),
            )
            if packet_type == "task.start":
                self._event_keys.clear()
        self._publish()
        try:
            receipt = self._orin.issue(packet_type, payload)
            if not receipt.accepted:
                raise OnboardCommandRejectedError(
                    f"Onboard komutu reddedildi: {receipt.message}",
                    receipt.response,
                )
            if packet_type.startswith("task.") or packet_type == "mission.status":
                self._apply_ack(receipt.response, task=task)
            return receipt
        except OnboardCommandRejectedError as exc:
            self._set_error(str(exc))
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
            self._set_error(message)
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
        mission_id = self._optional_int(payload.get("mission_id"))
        phase = str(payload.get("execution", "acknowledged")).strip().lower()
        if isinstance(snapshot, dict):
            mission_id = self._optional_int(snapshot.get("mission_id")) or mission_id
            phase = str(snapshot.get("phase", phase)).strip().lower()
        waiting = bool(payload.get("waiting_for_mode", False))
        with self._lock:
            self._state = replace(
                self._state,
                active_task=str(payload.get("task") or task or self._state.active_task).lower(),
                mission_id=mission_id or self._state.mission_id,
                phase=("starting" if phase == "starting" else phase or self._state.phase),
                waiting_for_guided=waiting,
                vehicle_mode=str(payload.get("vehicle_mode", self._state.vehicle_mode)).upper(),
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
            {"task": task, "mission_id": mission_id, "execution": phase},
        )
        self._publish()

    def _handle_transition(self, message: OrinMessage) -> dict[str, Any]:
        payload = message.payload
        current = str(payload.get("current", "unknown")).strip().lower()
        mission_id = self._optional_int(payload.get("mission_id"))
        task = str(payload.get("task", self.state.active_task)).strip().lower()
        reason = str(payload.get("reason", "")).strip()
        with self._lock:
            self._state = replace(
                self._state,
                active_task=task,
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
        execution = str(payload.get("execution") or snapshot.get("phase") or "failed").lower()
        mission_id = self._optional_int(payload.get("mission_id") or snapshot.get("mission_id"))
        with self._lock:
            self._state = replace(
                self._state,
                active_task=str(payload.get("task") or snapshot.get("task") or self._state.active_task).lower(),
                mission_id=mission_id or self._state.mission_id,
                phase=execution,
                waiting_for_guided=False,
                result=dict(snapshot),
                result_received=True,
                last_error=(str(snapshot.get("reason", "")) if execution == "failed" else self._state.last_error),
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
        self._process_event_payload(message.payload, source="live")
        return {}

    def _process_event_payload(
        self,
        payload: dict[str, Any],
        *,
        source: str,
        mission_id: int | None = None,
        sequence: int | None = None,
    ) -> None:
        event_name = str(payload.get("event", "UNKNOWN")).strip().upper()
        altitude = payload.get("altitude_m")
        event = OnboardMissionEvent(
            event=event_name,
            time_utc=str(payload.get("time_utc") or payload.get("timestamp") or ""),
            mission_id=mission_id or self.state.mission_id,
            sequence=sequence,
            source=source,
            qr_text=(str(payload["qr_text"]) if payload.get("qr_text") is not None else None),
            altitude_m=(float(altitude) if altitude is not None else None),
            fields=dict(payload),
        )
        with self._lock:
            if event.deduplication_key in self._event_keys:
                return
            self._event_keys.add(event.deduplication_key)
            timeline = (*self._state.timeline, event)[-self._TIMELINE_LIMIT :]
            self._state = replace(
                self._state,
                timeline=timeline,
                critical_event=(event_name if event_name in CRITICAL_KAMIKAZE_EVENTS else self._state.critical_event),
                qr_status=(
                    "cancelled"
                    if event_name == "SAFETY_FLOOR_BREACH"
                    and bool(payload.get("qr_cancelled"))
                    and self._state.qr_text is None
                    else self._state.qr_status
                ),
                waiting_for_guided=(True if event_name == "WAITING_FOR_GUIDED" else False if event_name == "GUIDED_READY" else self._state.waiting_for_guided),
            )
        if event_name in QR_EVENT_TYPES and event.qr_text:
            self._accept_qr(event)
        severity = "danger" if event.critical else "success" if event_name in QR_EVENT_TYPES else "warning" if event_name in {"ABORT", "FAULT"} else "info"
        self._log(
            "kamikaze.event",
            f"Kamikaze olayı: {event_name}",
            severity,
            {**dict(payload), "mission_id": event.mission_id, "source": source},
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
        with self._lock:
            was_connected = self._state.connected
            target_ready = self._state.target_ready
            target_revision = self._state.target_revision
            target = dict(self._state.target)
            if state.alive and not was_connected:
                target_ready = False
                target_revision = 0
                target = {}
            elif state.alive and not state.kamikaze_target_ready:
                target_ready = False
            self._state = replace(
                self._state,
                connected=state.alive,
                onboard_version=state.version,
                vehicle_mode=state.vehicle_mode,
                active_task=state.active_task or self._state.active_task,
                queued_tasks=tuple(state.queued_tasks),
                mission_id=(
                    state.active_missions[0]
                    if len(state.active_missions) == 1
                    else self._state.mission_id
                ),
                target_ready=target_ready,
                target_revision=(
                    state.kamikaze_target_revision
                    if state.kamikaze_target_ready
                    else target_revision
                ),
                target=target,
                last_error=state.last_error if not state.alive else self._state.last_error,
            )
        self._publish()
        if state.alive and not was_connected:
            self._start_recovery()

    def _start_recovery(self) -> None:
        with self._lock:
            if self._recovery_running or not self._started or not self._orin.connected:
                return
            self._recovery_running = True
        threading.Thread(target=self._recover, name="OnboardMissionRecovery", daemon=True).start()

    def _recover(self) -> None:
        try:
            receipt = self._orin.issue("mission.list", {})
            if receipt.accepted:
                self._apply_mission_list(receipt.response.get("missions", []))
            mission_ids = set(self._event_cursors)
            if self.state.mission_id is not None:
                mission_ids.add(self.state.mission_id)
            for mission_id in mission_ids:
                after_sequence = self._event_cursors.get(mission_id, 0)
                response = self._orin.issue(
                    "mission.events",
                    {
                        "mission": mission_id,
                        "event_types": ["log", "transition", "error"],
                        "minimum_level": "DEBUG",
                        "after_sequence": after_sequence,
                        "limit": 200,
                    },
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

    def _apply_mission_list(self, missions: Any) -> None:
        if not isinstance(missions, list):
            return
        active = next(
            (
                item for item in missions
                if isinstance(item, dict)
                and str(item.get("task", "")).lower() == "kamikaze.execute"
                and str(item.get("phase", "")).lower() not in {"stopped", "succeeded", "failed", "cancelled"}
            ),
            None,
        )
        if active is None:
            return
        with self._lock:
            self._state = replace(
                self._state,
                active_task="kamikaze.execute",
                mission_id=self._optional_int(active.get("mission_id")),
                phase=str(active.get("phase", self._state.phase)).lower(),
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
            if event_name in QR_EVENT_TYPES:
                self._process_event_payload(
                    dict(fields),
                    source="recovery",
                    mission_id=mission_id,
                    sequence=sequence,
                )

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._state = replace(self._state, last_error=message)
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
        normalized = str(task).strip()
        if not normalized:
            raise ValueError("Görev adı boş olamaz")
        return TASK_ALIASES.get(normalized.upper(), normalized.lower())

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None


__all__ = ["OnboardMissionService", "TASK_ALIASES"]
