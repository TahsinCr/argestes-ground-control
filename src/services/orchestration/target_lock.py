from __future__ import annotations

import threading
import time
from dataclasses import replace

from src.core.events import EventStream, Subscription
from src.models.commands import CommandResult
from src.models.competition import LockEvent
from src.models.target_lock import TargetLockAcquisition, TargetLockWorkflowState
from src.services.contracts.commands import CommandService
from src.services.contracts.competition import CompetitionService
from src.services.contracts.target_lock import TargetLockService, TargetLockVisionService
from src.services.logging_service import ServiceLogger


class TargetLockWorkflow(TargetLockService):
    """Target Lock komutunu görüntü işlemeye ve TEKNOFEST teslimine bağlar."""

    _TASK_CODES = frozenset(
        {
            "TARGET_LOCK",
            "KAMIKAZE",
            "WAYPOINT_TRACK",
            "AUTO_TAKEOFF",
            "AUTO_LAND",
            "SURVEY_AREA",
        }
    )

    def __init__(
        self,
        commands: CommandService,
        vision: TargetLockVisionService,
        competition: CompetitionService,
        logger: ServiceLogger | None = None,
        *,
        retry_interval_s: float = 0.25,
        submission_deadline_s: float = 2.0,
    ) -> None:
        self._commands = commands
        self._vision = vision
        self._competition = competition
        self._logger = logger
        self._retry_interval_s = float(retry_interval_s)
        self._submission_deadline_s = float(submission_deadline_s)
        self._state = TargetLockWorkflowState()
        self._changed = EventStream[TargetLockWorkflowState]()
        self._subscriptions: list[Subscription] = []
        self._lock = threading.RLock()
        self._generation = 0
        self._submit_thread: threading.Thread | None = None

    @property
    def state(self) -> TargetLockWorkflowState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventStream[TargetLockWorkflowState]:
        return self._changed

    def start(self) -> None:
        with self._lock:
            if self._subscriptions:
                return
            self._subscriptions = [
                self._commands.completed.subscribe(self._on_command_completed),
                self._vision.target_acquired.subscribe(self._on_target_acquired),
            ]
        self._vision.set_target_lock_active(False)

    def stop(self) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
            thread = self._submit_thread
        for subscription in subscriptions:
            subscription.cancel()
        self.deactivate()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._submission_deadline_s + 1.0)

    def activate(self) -> None:
        with self._lock:
            self._generation += 1
            self._state = TargetLockWorkflowState(active=True)
            state = replace(self._state)
        self._vision.set_target_lock_active(False)
        self._vision.set_target_lock_active(True)
        self._changed.publish_isolated(state)
        self._event(
            "target_lock.mode_started",
            "Target Lock görevi etkin; YOLO hedef takibi başlatıldı",
            severity="success",
        )

    def deactivate(self) -> None:
        with self._lock:
            was_active = self._state.active
            self._generation += 1
            self._state = TargetLockWorkflowState()
            state = replace(self._state)
        self._vision.set_target_lock_active(False)
        self._changed.publish_isolated(state)
        if was_active:
            self._event(
                "target_lock.mode_stopped",
                "Target Lock görevi kapatıldı; hedef takibi durduruldu",
            )

    def _on_command_completed(self, result: CommandResult) -> None:
        if not result.accepted:
            return
        task = str(result.details.get("task", "")).strip().upper()
        if not task and result.request.code == "SET_MISSION_TASK":
            task = str(result.request.parameters.get("task", "")).strip().upper()
        if not task and result.request.code in self._TASK_CODES:
            task = result.request.code
        if task == "TARGET_LOCK":
            self.activate()
        elif task in self._TASK_CODES:
            self.deactivate()

    def _on_target_acquired(self, acquisition: TargetLockAcquisition) -> None:
        with self._lock:
            if not self._state.active or self._state.sending or self._state.submitted:
                return
            generation = self._generation
            self._state = replace(
                self._state,
                sending=True,
                last_error="",
                completed_at=acquisition.ended_at,
            )
            state = replace(self._state)
            thread = threading.Thread(
                target=self._submit,
                args=(generation, acquisition),
                name="TargetLockSubmission",
                daemon=True,
            )
            self._submit_thread = thread
        self._changed.publish_isolated(state)
        thread.start()

    def _submit(
        self,
        generation: int,
        acquisition: TargetLockAcquisition,
    ) -> None:
        event = LockEvent(
            ended_at=acquisition.ended_at,
            autonomous=acquisition.autonomous,
            vehicle_source="opencv-target-lock-simulation",
        )
        deadline = time.monotonic() + self._submission_deadline_s
        last_error = ""
        while True:
            with self._lock:
                if generation != self._generation:
                    return
            try:
                self._competition.submit_lock_event(event)
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(self._retry_interval_s, remaining))
                continue
            with self._lock:
                if generation != self._generation:
                    return
                self._state = replace(
                    self._state,
                    sending=False,
                    submitted=True,
                    last_error="",
                )
                state = replace(self._state)
            self._changed.publish_isolated(state)
            self._event(
                "target_lock.submitted",
                "Kilitlenme bilgisi TEKNOFEST sunucusuna kaydedildi",
                severity="success",
                details={
                    "track_id": acquisition.target.track_id,
                    "ended_at": acquisition.ended_at.isoformat(),
                },
            )
            return

        with self._lock:
            if generation != self._generation:
                return
            self._state = replace(
                self._state,
                sending=False,
                last_error=last_error,
            )
            state = replace(self._state)
        self._changed.publish_isolated(state)
        self._event(
            "target_lock.submit_failed",
            f"Kilitlenme bilgisi 2 saniye içinde gönderilemedi: {last_error}",
            severity="danger",
            details={"track_id": acquisition.target.track_id},
        )

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
