from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.core.abstracts import Model


@dataclass(frozen=True, slots=True)
class TargetBoundingBox(Model):
    x: int
    y: int
    width: int
    height: int
    confidence: float
    track_id: int

    def __post_init__(self) -> None:
        if min(self.x, self.y, self.width, self.height, self.track_id) < 0:
            raise ValueError("Hedef kutusu değerleri negatif olamaz")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Hedef kutusu boyutları pozitif olmalı")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Hedef güveni 0..1 aralığında olmalı")

    @property
    def center_x(self) -> int:
        return round(self.x + self.width / 2.0)

    @property
    def center_y(self) -> int:
        return round(self.y + self.height / 2.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TargetLockAcquisition(Model):
    ended_at: datetime
    target: TargetBoundingBox
    autonomous: bool = True

    def __post_init__(self) -> None:
        if self.ended_at.tzinfo is None:
            raise ValueError("Kilitlenme bitiş zamanı saat dilimi içermeli")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ended_at": self.ended_at.isoformat(),
            "target": self.target.to_dict(),
            "autonomous": self.autonomous,
        }


@dataclass(frozen=True, slots=True)
class TargetLockVisionState(Model):
    active: bool = False
    phase: str = "IDLE"
    target: TargetBoundingBox | None = None
    strike_zone: tuple[int, int, int, int] | None = None
    in_strike_zone: bool = False
    progress: float = 0.0
    lock_elapsed_s: float = 0.0
    window_elapsed_s: float = 0.0
    lock_required_s: float = 4.0
    window_duration_s: float = 5.0
    locked: bool = False
    feedback: str = ""
    model_ready: bool = False
    model_backend: str = ""
    last_error: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("Kilit ilerlemesi 0..1 aralığında olmalı")
        if min(
            self.lock_elapsed_s,
            self.window_elapsed_s,
            self.lock_required_s,
            self.window_duration_s,
        ) < 0.0:
            raise ValueError("Kilit süreleri negatif olamaz")
        if self.feedback not in {"", "success", "failure"}:
            raise ValueError("Kilit geri bildirimi boş, success veya failure olmalı")

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "phase": self.phase,
            "target": self.target.to_dict() if self.target else None,
            "strike_zone": list(self.strike_zone) if self.strike_zone else None,
            "in_strike_zone": self.in_strike_zone,
            "progress": self.progress,
            "lock_elapsed_s": self.lock_elapsed_s,
            "window_elapsed_s": self.window_elapsed_s,
            "lock_required_s": self.lock_required_s,
            "window_duration_s": self.window_duration_s,
            "locked": self.locked,
            "feedback": self.feedback,
            "model_ready": self.model_ready,
            "model_backend": self.model_backend,
            "last_error": self.last_error,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TargetLockWorkflowState(Model):
    active: bool = False
    sending: bool = False
    submitted: bool = False
    last_error: str = ""
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "sending": self.sending,
            "submitted": self.submitted,
            "last_error": self.last_error,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }
