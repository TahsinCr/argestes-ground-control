from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime

from src.core.events import EventStream, Subscription
from src.models.commands import CommandResult
from src.models.competition import KamikazeEvent, KamikazeQrWorkflowState
from src.models.video import QrDetectionSnapshot
from src.services.contracts.commands import CommandService
from src.services.contracts.competition import CompetitionService
from src.services.contracts.kamikaze import KamikazeQrService
from src.services.contracts.video import QrCodeService
from src.services.logging_service import ServiceLogger


class KamikazeQrWorkflow(KamikazeQrService):
    """Kamikaze görevi sırasında QR sonucunu TEKNOFEST'e teslim eder."""

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
        qr: QrCodeService,
        competition: CompetitionService,
        logger: ServiceLogger | None = None,
        *,
        retry_interval_s: float = 3.0,
    ) -> None:
        if retry_interval_s <= 0:
            raise ValueError("Kamikaze QR yeniden deneme aralığı pozitif olmalı")
        self._commands = commands
        self._qr = qr
        self._competition = competition
        self._logger = logger
        self._retry_interval_s = float(retry_interval_s)
        self._state = KamikazeQrWorkflowState()
        self._changed = EventStream[KamikazeQrWorkflowState]()
        self._subscriptions: list[Subscription] = []
        self._lock = threading.RLock()
        self._submit_thread: threading.Thread | None = None
        self._retry_not_before = 0.0
        self._generation = 0

    @property
    def state(self) -> KamikazeQrWorkflowState:
        with self._lock:
            return replace(self._state)

    @property
    def changed(self) -> EventStream[KamikazeQrWorkflowState]:
        return self._changed

    def start(self) -> None:
        with self._lock:
            if self._subscriptions:
                return
            self._subscriptions = [
                self._commands.completed.subscribe(self._on_command_completed),
                self._qr.qr_changed.subscribe(self._on_qr_snapshot),
            ]
        self._qr.set_qr_active(False)

    def stop(self) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions)
            self._subscriptions.clear()
            thread = self._submit_thread
        for subscription in subscriptions:
            subscription.cancel()
        self.deactivate()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=4.0)

    def activate(self, *, started_at: datetime | None = None) -> None:
        start = started_at or datetime.now(UTC)
        if start.tzinfo is None:
            raise ValueError("Kamikaze başlangıç zamanı saat dilimi içermeli")
        with self._lock:
            self._generation += 1
            self._retry_not_before = 0.0
            self._state = KamikazeQrWorkflowState(
                active=True,
                started_at=start,
            )
            state = replace(self._state)
        # Aynı KAMIKAZE komutu yeni bir görev oturumudur; önceki QR kabul
        # belleği yalnızca burada sıfırlanır.
        self._qr.set_qr_active(False)
        self._qr.set_qr_active(True)
        self._changed.publish_isolated(state)
        self._event(
            "kamikaze.qr_mode_started",
            "Kamikaze modu etkin; WeChat QR taraması başlatıldı",
            severity="success",
            details={"started_at": start.isoformat()},
        )

    def deactivate(self) -> None:
        with self._lock:
            was_active = self._state.active
            self._generation += 1
            self._retry_not_before = 0.0
            self._state = KamikazeQrWorkflowState()
            state = replace(self._state)
        self._qr.set_qr_active(False)
        self._changed.publish_isolated(state)
        if was_active:
            self._event(
                "kamikaze.qr_mode_stopped",
                "Kamikaze modu kapatıldı; QR taraması durduruldu",
            )

    def _on_command_completed(self, result: CommandResult) -> None:
        if not result.accepted:
            return
        task = str(result.details.get("task", "")).strip().upper()
        if not task and result.request.code == "SET_MISSION_TASK":
            task = str(result.request.parameters.get("task", "")).strip().upper()
        if not task and result.request.code in self._TASK_CODES:
            task = result.request.code
        if task == "KAMIKAZE":
            self.activate(started_at=result.request.timestamp)
        elif task in self._TASK_CODES:
            self.deactivate()

    def _on_qr_snapshot(self, snapshot: QrDetectionSnapshot) -> None:
        decoded = next(
            (item for item in snapshot.decoded if item.data.strip()),
            None,
        )
        if decoded is None:
            return
        with self._lock:
            now_monotonic = time.monotonic()
            if (
                not self._state.active
                or self._state.submitted
                or self._state.sending
                or now_monotonic < self._retry_not_before
                or self._state.started_at is None
            ):
                return
            generation = self._generation
            started_at = self._state.started_at
            ended_at = datetime.now(UTC)
            qr_text = decoded.data.strip()
            self._state = replace(
                self._state,
                qr_text=qr_text,
                sending=True,
                last_error="",
            )
            state = replace(self._state)
            thread = threading.Thread(
                target=self._submit,
                args=(generation, started_at, ended_at, qr_text),
                name="KamikazeQrSubmission",
                daemon=True,
            )
            self._submit_thread = thread
        self._changed.publish_isolated(state)
        self._event(
            "kamikaze.qr_captured",
            f"Kamikaze QR verisi yakalandı: {qr_text}",
            severity="success",
            details={"track_id": decoded.track_id, "qr_text": qr_text},
        )
        thread.start()

    def _submit(
        self,
        generation: int,
        started_at: datetime,
        ended_at: datetime,
        qr_text: str,
    ) -> None:
        event = KamikazeEvent(
            started_at=started_at,
            ended_at=ended_at,
            qr_text=qr_text,
            vehicle_source="opencv-kamikaze-simulation",
        )
        try:
            self._competition.submit_kamikaze_event(event)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            with self._lock:
                if generation != self._generation:
                    return
                self._retry_not_before = time.monotonic() + self._retry_interval_s
                self._state = replace(
                    self._state,
                    sending=False,
                    last_error=message,
                )
                state = replace(self._state)
            self._changed.publish_isolated(state)
            self._event(
                "kamikaze.qr_submit_failed",
                f"Kamikaze QR verisi TEKNOFEST sunucusuna gönderilemedi: {message}",
                severity="danger",
                details={"qr_text": qr_text, "retry_interval_s": self._retry_interval_s},
            )
            return
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
        self._qr.mark_qr_submitted(qr_text)
        self._changed.publish_isolated(state)
        self._event(
            "kamikaze.qr_submitted",
            f"Kamikaze QR verisi TEKNOFEST sunucusuna kaydedildi: {qr_text}",
            severity="success",
            details={
                "qr_text": qr_text,
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
            },
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
