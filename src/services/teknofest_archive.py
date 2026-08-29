from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import threading
from typing import Any, TextIO

from src.core.events import Subscription
from src.models.competition import (
    CompetitionClockSample,
    CompetitionConnectionState,
    CompetitionServerConfig,
    CompetitorTelemetry,
    HssZone,
    QrCoordinate,
)
from src.services.abstracts.competition import CompetitionService
from src.services.logging_service import ServiceLogger


class TeknofestSessionArchiveService:
    """Persist TEKNOFEST input and lifecycle events for one application run."""

    FORMAT_VERSION = 1
    ARCHIVE_FILE_NAME = "teknofest.jsonl"
    MANIFEST_FILE_NAME = "session.json"

    def __init__(
        self,
        base_dir: Path,
        competition: CompetitionService,
        logger: ServiceLogger | None = None,
    ) -> None:
        self._sessions_dir = Path(base_dir) / "data" / "sessions"
        self._competition = competition
        self._logger = logger
        self._lock = threading.RLock()
        self._subscriptions: list[Subscription] = []
        self._stream: TextIO | None = None
        self._session_dir: Path | None = None
        self._started_at: datetime | None = None
        self._sequence = 0
        self._last_state_signature = ""
        self._last_config_signature = ""
        self._last_write_error = ""

    @property
    def session_dir(self) -> Path | None:
        with self._lock:
            return self._session_dir

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            started_at = datetime.now(UTC)
            session_dir = self._allocate_session_dir(started_at)
            session_dir.mkdir(parents=True, exist_ok=False)
            self._session_dir = session_dir
            self._started_at = started_at
            self._sequence = 0
            self._last_state_signature = ""
            self._last_config_signature = ""
            self._last_write_error = ""
            self._stream = (session_dir / self.ARCHIVE_FILE_NAME).open(
                "a", encoding="utf-8", buffering=1
            )

        self._subscriptions = [
            self._competition.config_updated.subscribe(self._record_config),
            self._competition.changed.subscribe(self._record_state),
            self._competition.clock_updated.subscribe(self._record_clock),
            self._competition.competitors_updated.subscribe(self._record_competitors),
            self._competition.hss_updated.subscribe(self._record_hss),
            self._competition.qr_updated.subscribe(self._record_qr),
        ]
        self._append("session.started", {"format_version": self.FORMAT_VERSION})
        self._record_config(self._competition.config, force=True)
        self._record_state(self._competition.state, force=True)
        self._record_competitors(self._competition.competitors, initial=True)
        self._record_hss(self._competition.hss_zones, initial=True)
        self._record_qr(self._competition.qr_coordinate, initial=True)
        self._write_manifest()
        if self._logger is not None:
            self._logger.event(
                "competition.archive_started",
                "TEKNOFEST oturum arşivi başlatıldı",
                severity="success",
                details={"path": str(self.session_dir)},
            )

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
        self._append("session.stopped", {})
        self._write_manifest(ended_at=datetime.now(UTC))
        with self._lock:
            stream = self._stream
            self._stream = None
            session_dir = self._session_dir
        if stream is not None:
            stream.close()
        if self._logger is not None and session_dir is not None:
            self._logger.event(
                "competition.archive_stopped",
                "TEKNOFEST oturum arşivi kapatıldı",
                details={"path": str(session_dir), "record_count": self._sequence},
            )

    close = stop

    def _allocate_session_dir(self, started_at: datetime) -> Path:
        day_dir = self._sessions_dir / started_at.strftime("%Y-%m-%d")
        stem = started_at.strftime("%H-%M-%S")
        candidate = day_dir / stem
        suffix = 1
        while candidate.exists():
            candidate = day_dir / f"{stem}-{suffix:02d}"
            suffix += 1
        return candidate

    def _server_identity(self) -> dict[str, Any]:
        config = self._competition.config
        return {
            "identifier": config.identifier,
            "name": config.name,
            "endpoint": config.base_url,
            "team_number": config.team_number,
        }

    def _append(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            if self._stream is None:
                return
            self._sequence += 1
            record = {
                "format_version": self.FORMAT_VERSION,
                "sequence": self._sequence,
                "recorded_at": datetime.now(UTC).isoformat(),
                "event": event,
                "server": self._server_identity(),
                "data": data,
            }
            try:
                self._stream.write(
                    json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    + "\n"
                )
                self._stream.flush()
                self._last_write_error = ""
            except (OSError, TypeError, ValueError) as exc:
                self._sequence -= 1
                signature = f"{type(exc).__name__}: {exc}"
                if signature != self._last_write_error and self._logger is not None:
                    self._last_write_error = signature
                    self._logger.event(
                        "competition.archive_write_failed",
                        f"TEKNOFEST oturum arşivine yazılamadı: {exc}",
                        severity="danger",
                        details={"path": str(self._session_dir), "event": event},
                    )

    def _record_config(self, config: CompetitionServerConfig, *, force: bool = False) -> None:
        data = {
            "identifier": config.identifier,
            "name": config.name,
            "endpoint": config.base_url,
            "username": config.username,
            "team_number": config.team_number,
            "timeout_s": config.timeout_s,
            "auto_connect": config.auto_connect,
        }
        signature = json.dumps(data, sort_keys=True, ensure_ascii=False)
        with self._lock:
            if not force and signature == self._last_config_signature:
                return
            self._last_config_signature = signature
        self._append("server.selected", data)

    def _record_state(
        self,
        state: CompetitionConnectionState,
        *,
        force: bool = False,
    ) -> None:
        data = state.to_dict()
        semantic = dict(data)
        semantic.pop("last_success_at", None)
        signature = json.dumps(semantic, sort_keys=True, ensure_ascii=False)
        with self._lock:
            if not force and signature == self._last_state_signature:
                return
            self._last_state_signature = signature
        self._append("connection.state", data)

    def _record_clock(self, sample: CompetitionClockSample) -> None:
        server_time = sample.server_time
        self._append(
            "server.clock",
            {
                "sunucusaati": {
                    "gun": server_time.day,
                    "saat": server_time.hour,
                    "dakika": server_time.minute,
                    "saniye": server_time.second,
                    "milisaniye": server_time.millisecond,
                },
                "resolved_at": sample.resolved_at.isoformat(),
                "round_trip_ms": sample.rtt_s * 1000.0,
            },
        )

    def _record_competitors(
        self,
        competitors: tuple[CompetitorTelemetry, ...],
        *,
        initial: bool = False,
    ) -> None:
        self._append(
            "competitors.snapshot",
            {
                "initial": initial,
                "count": len(competitors),
                "vehicles": [item.to_dict() for item in competitors],
            },
        )

    def _record_hss(
        self,
        zones: tuple[HssZone, ...],
        *,
        initial: bool = False,
    ) -> None:
        self._append(
            "hss.snapshot" if initial else ("hss.updated" if zones else "hss.cleared"),
            {
                "initial": initial,
                "count": len(zones),
                "zones": [zone.to_dict() for zone in zones],
            },
        )

    def _record_qr(self, coordinate: QrCoordinate | None, *, initial: bool = False) -> None:
        self._append(
            "qr.snapshot" if initial else ("qr.updated" if coordinate is not None else "qr.cleared"),
            {
                "initial": initial,
                "coordinate": None if coordinate is None else coordinate.to_dict(),
            },
        )

    def _write_manifest(self, *, ended_at: datetime | None = None) -> None:
        with self._lock:
            if self._session_dir is None or self._started_at is None:
                return
            manifest = {
                "format_version": self.FORMAT_VERSION,
                "kind": "teknofest-session-archive",
                "started_at": self._started_at.isoformat(),
                "ended_at": None if ended_at is None else ended_at.isoformat(),
                "record_count": self._sequence,
                "archive_file": self.ARCHIVE_FILE_NAME,
            }
            path = self._session_dir / self.MANIFEST_FILE_NAME
        try:
            path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            if self._logger is not None:
                self._logger.event(
                    "competition.archive_manifest_failed",
                    f"TEKNOFEST oturum bilgisi yazılamadı: {exc}",
                    severity="warning",
                    details={"path": str(path)},
                )


__all__ = ["TeknofestSessionArchiveService"]
