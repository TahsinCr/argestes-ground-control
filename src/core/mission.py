from __future__ import annotations

import re
import time
from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from src.core.abstracts import Service


_MISSION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


class MissionError(RuntimeError):
    """Base error for mission lifecycle and coordination failures."""


class MissionRegistrationError(MissionError):
    """Raised when a mission definition is invalid or already registered."""


class MissionPermissionError(MissionError):
    """Raised when one mission has insufficient authority over another."""


class MissionConflictError(MissionError):
    """Raised when active missions or exclusive resources conflict."""


class MissionTransitionError(MissionError):
    """Raised when a mission lifecycle transition is not valid."""


class MissionPriority(IntEnum):
    """Common priority bands; smaller numbers carry greater authority."""

    CRITICAL = 0
    HIGH = 100
    NORMAL = 500
    LOW = 900
    BACKGROUND = 1_000


class MissionPhase(StrEnum):
    REGISTERED = "registered"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def active(self) -> bool:
        return self in {
            MissionPhase.STARTING,
            MissionPhase.RUNNING,
            MissionPhase.PAUSING,
            MissionPhase.PAUSED,
            MissionPhase.STOPPING,
        }

    @property
    def terminal(self) -> bool:
        return self in {
            MissionPhase.STOPPED,
            MissionPhase.SUCCEEDED,
            MissionPhase.FAILED,
            MissionPhase.CANCELLED,
        }


class MissionConflictPolicy(StrEnum):
    REJECT = "reject"
    QUEUE = "queue"
    PREEMPT_LOWER = "preempt_lower"


class MissionPrerequisitePolicy(StrEnum):
    """Define what happens while required missions have not succeeded."""

    REJECT = "reject"
    QUEUE = "queue"


class MissionEventLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class MissionEventType(StrEnum):
    MANAGER = "manager"
    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    COMMAND = "command"
    TRANSITION = "transition"
    PROGRESS = "progress"
    CHECKPOINT = "checkpoint"
    CHAIN = "chain"
    RETRY = "retry"
    LOG = "log"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MissionRetryPolicy:
    max_attempts: int = 1
    delay_seconds: float = 0.0
    backoff_multiplier: float = 1.0
    max_delay_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Mission retry attempts must be at least one")
        if self.delay_seconds < 0:
            raise ValueError("Mission retry delay cannot be negative")
        if self.backoff_multiplier < 1:
            raise ValueError("Mission retry backoff must be at least one")
        if self.max_delay_seconds is not None and self.max_delay_seconds < 0:
            raise ValueError("Mission retry maximum delay cannot be negative")

    def delay_for_attempt(self, completed_attempt: int) -> float:
        exponent = max(0, int(completed_attempt) - 1)
        delay = self.delay_seconds * (self.backoff_multiplier**exponent)
        if self.max_delay_seconds is not None:
            delay = min(delay, self.max_delay_seconds)
        return delay


@dataclass(frozen=True, slots=True)
class MissionDescriptor:
    mission_id: str
    name: str
    priority: int = int(MissionPriority.NORMAL)
    resources: frozenset[str] = field(default_factory=frozenset)
    blocks: frozenset[str] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)
    prerequisites: frozenset[str] = field(default_factory=frozenset)
    conflict_policy: MissionConflictPolicy = MissionConflictPolicy.REJECT
    prerequisite_policy: MissionPrerequisitePolicy = MissionPrerequisitePolicy.REJECT
    tick_interval: float = 0.1
    timeout_seconds: float | None = None
    queue_timeout_seconds: float | None = None
    retry: MissionRetryPolicy = field(default_factory=MissionRetryPolicy)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mission_id = self.mission_id.strip().lower()
        name = self.name.strip()
        if not _MISSION_ID_PATTERN.fullmatch(mission_id):
            raise ValueError(
                "Mission ID must start with a letter and contain 2-64 lowercase "
                "letters, numbers, dots, underscores or hyphens"
            )
        if not name:
            raise ValueError("Mission name cannot be empty")
        if int(self.priority) < 0:
            raise ValueError("Mission priority cannot be negative")
        if self.tick_interval <= 0:
            raise ValueError("Mission tick interval must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("Mission timeout must be positive")
        if self.queue_timeout_seconds is not None and self.queue_timeout_seconds <= 0:
            raise ValueError("Mission queue timeout must be positive")
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "resources", normalize_names(self.resources))
        object.__setattr__(self, "blocks", normalize_mission_ids(self.blocks))
        object.__setattr__(self, "tags", normalize_names(self.tags))
        prerequisites = normalize_mission_ids(self.prerequisites)
        if mission_id in prerequisites:
            raise ValueError("Mission cannot require itself")
        object.__setattr__(self, "prerequisites", prerequisites)
        object.__setattr__(
            self,
            "conflict_policy",
            MissionConflictPolicy(self.conflict_policy),
        )
        object.__setattr__(
            self,
            "prerequisite_policy",
            MissionPrerequisitePolicy(self.prerequisite_policy),
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class MissionSnapshot:
    descriptor: MissionDescriptor
    phase: MissionPhase = MissionPhase.REGISTERED
    generation: int = 0
    attempt: int = 0
    progress: float = 0.0
    reason: str = ""
    result: Mapping[str, Any] = field(default_factory=dict)
    checkpoints: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    registered_at: float = field(default_factory=time.time)
    queued_at: float | None = None
    next_retry_at: float | None = None
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.progress) <= 1.0:
            raise ValueError("Mission progress must be between zero and one")
        object.__setattr__(self, "phase", MissionPhase(self.phase))
        object.__setattr__(self, "progress", float(self.progress))
        object.__setattr__(
            self,
            "result",
            MappingProxyType(dict(self.result)),
        )
        object.__setattr__(
            self,
            "checkpoints",
            MappingProxyType(
                {
                    str(name): MappingProxyType(dict(value))
                    for name, value in self.checkpoints.items()
                }
            ),
        )

    def evolve(self, **changes: Any) -> "MissionSnapshot":
        changes.setdefault("updated_at", time.time())
        return replace(self, **changes)

    @property
    def duration_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)


@dataclass(frozen=True, slots=True)
class MissionEvent:
    event_type: MissionEventType
    message: str
    level: MissionEventLevel = MissionEventLevel.INFO
    mission_id: str | None = None
    requester_id: str | None = None
    generation: int = 0
    fields: Mapping[str, Any] = field(default_factory=dict)
    sequence: int = 0
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        message = str(self.message).strip()
        if not message:
            raise ValueError("Mission event message cannot be empty")
        mission_id = _normalize_optional_mission_id(self.mission_id)
        requester_id = _normalize_optional_mission_id(self.requester_id)
        if self.sequence < 0:
            raise ValueError("Mission event sequence cannot be negative")
        if self.generation < 0:
            raise ValueError("Mission event generation cannot be negative")
        object.__setattr__(self, "event_type", MissionEventType(self.event_type))
        object.__setattr__(self, "level", MissionEventLevel(self.level))
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "requester_id", requester_id)
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True, slots=True)
class MissionEventQuery:
    mission_ids: frozenset[str] = field(default_factory=frozenset)
    event_types: frozenset[MissionEventType] = field(default_factory=frozenset)
    minimum_level: MissionEventLevel = MissionEventLevel.DEBUG
    after_sequence: int = 0
    limit: int = 200

    def __post_init__(self) -> None:
        if self.after_sequence < 0:
            raise ValueError("Mission event cursor cannot be negative")
        if self.limit <= 0:
            raise ValueError("Mission event query limit must be positive")
        object.__setattr__(
            self,
            "mission_ids",
            normalize_mission_ids(self.mission_ids),
        )
        object.__setattr__(
            self,
            "event_types",
            frozenset(MissionEventType(value) for value in self.event_types),
        )
        object.__setattr__(
            self,
            "minimum_level",
            MissionEventLevel(self.minimum_level),
        )

    def matches(self, event: MissionEvent) -> bool:
        return (
            event.sequence > self.after_sequence
            and event.level >= self.minimum_level
            and (not self.mission_ids or event.mission_id in self.mission_ids)
            and (not self.event_types or event.event_type in self.event_types)
        )


@dataclass(frozen=True, slots=True)
class MissionManagerSnapshot:
    running: bool = False
    registered_missions: tuple[str, ...] = ()
    active_missions: tuple[str, ...] = ()
    queued_missions: tuple[str, ...] = ()
    paused_missions: tuple[str, ...] = ()
    resource_owners: Mapping[str, str] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_owners",
            MappingProxyType(dict(self.resource_owners)),
        )


@dataclass(frozen=True, slots=True)
class MissionTransition:
    mission_id: str
    previous: MissionPhase
    current: MissionPhase
    requester_id: str | None
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class MissionChain:
    chain_id: str
    mission_ids: tuple[str, ...]
    stop_on_failure: bool = True

    def __post_init__(self) -> None:
        chain_id = self.chain_id.strip().lower()
        if not _MISSION_ID_PATTERN.fullmatch(chain_id):
            raise ValueError("Mission chain ID is invalid")
        mission_ids = tuple(
            str(mission_id).strip().lower()
            for mission_id in self.mission_ids
            if str(mission_id).strip()
        )
        if not mission_ids:
            raise ValueError("Mission chain must contain at least one mission")
        invalid = sorted(
            mission_id
            for mission_id in mission_ids
            if not _MISSION_ID_PATTERN.fullmatch(mission_id)
        )
        if invalid:
            raise ValueError(f"Invalid mission IDs: {', '.join(invalid)}")
        if len(set(mission_ids)) != len(mission_ids):
            raise ValueError("Mission chain cannot contain duplicate missions")
        object.__setattr__(self, "chain_id", chain_id)
        object.__setattr__(self, "mission_ids", mission_ids)


@dataclass(frozen=True, slots=True)
class MissionChainSnapshot:
    chain: MissionChain
    current_index: int = 0
    active: bool = False
    completed: bool = False
    failed: bool = False
    reason: str = ""

    @property
    def current_mission_id(self) -> str | None:
        if not self.active or self.current_index >= len(self.chain.mission_ids):
            return None
        return self.chain.mission_ids[self.current_index]


@runtime_checkable
class MissionControl(Protocol):
    """Capability passed to a mission for controlled manager operations."""

    @property
    def mission_id(self) -> str: ...
    @property
    def stop_requested(self) -> bool: ...

    def snapshot(self, mission_id: str | None = None) -> MissionSnapshot: ...
    def snapshots(self) -> tuple[MissionSnapshot, ...]: ...
    def wait_for_stop(self, timeout: float | None = None) -> bool: ...
    def start(self, mission_id: str, *, reason: str = "") -> MissionSnapshot: ...
    def pause(self, mission_id: str, *, reason: str = "") -> MissionSnapshot: ...
    def resume(self, mission_id: str, *, reason: str = "") -> MissionSnapshot: ...
    def stop(self, mission_id: str, *, reason: str = "") -> MissionSnapshot: ...
    def cancel(self, mission_id: str, *, reason: str = "") -> MissionSnapshot: ...
    def complete(self, result: Mapping[str, Any] | None = None) -> MissionSnapshot: ...
    def fail(self, reason: str, *, retryable: bool = False) -> MissionSnapshot: ...
    def progress(self, value: float, *, reason: str = "") -> MissionSnapshot: ...
    def checkpoint(
        self,
        name: str,
        values: Mapping[str, Any] | None = None,
    ) -> MissionSnapshot: ...
    def log(
        self,
        level: MissionEventLevel,
        message: str,
        **fields: Any,
    ) -> MissionEvent: ...


class Mission(Service, ABC):
    """Base mission with manager-owned lifecycle and overridable hooks."""

    def __init__(self, descriptor: MissionDescriptor) -> None:
        self.descriptor = descriptor
        self._control: MissionControl | None = None

    @property
    def control(self) -> MissionControl:
        if self._control is None:
            raise MissionError(
                f"Mission {self.descriptor.mission_id!r} is not bound to a manager"
            )
        return self._control

    def bind_control(self, control: MissionControl) -> None:
        if self._control is not None and self._control is not control:
            raise MissionRegistrationError(
                f"Mission {self.descriptor.mission_id!r} is already bound"
            )
        self._control = control

    @property
    def stop_requested(self) -> bool:
        return self.control.stop_requested

    def log(
        self,
        level: MissionEventLevel,
        message: str,
        **fields: Any,
    ) -> MissionEvent:
        return self.control.log(level, message, **fields)

    def debug(self, message: str, **fields: Any) -> MissionEvent:
        return self.log(MissionEventLevel.DEBUG, message, **fields)

    def info(self, message: str, **fields: Any) -> MissionEvent:
        return self.log(MissionEventLevel.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> MissionEvent:
        return self.log(MissionEventLevel.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> MissionEvent:
        return self.log(MissionEventLevel.ERROR, message, **fields)

    def checkpoint(
        self,
        name: str,
        **values: Any,
    ) -> MissionSnapshot:
        return self.control.checkpoint(name, values)

    def update_progress(
        self,
        value: float,
        *,
        reason: str = "",
    ) -> MissionSnapshot:
        return self.control.progress(value, reason=reason)

    def complete(
        self,
        result: Mapping[str, Any] | None = None,
    ) -> MissionSnapshot:
        return self.control.complete(result)

    def fail(
        self,
        reason: str,
        *,
        retryable: bool = False,
    ) -> MissionSnapshot:
        return self.control.fail(reason, retryable=retryable)

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        return self.control.wait_for_stop(timeout)

    def start(self) -> None:
        self.on_start()

    def stop(self) -> None:
        self.on_stop()

    def pause(self) -> None:
        self.on_pause()

    def resume(self) -> None:
        self.on_resume()

    def tick(self, elapsed_seconds: float) -> None:
        self.on_tick(elapsed_seconds)

    def on_start(self) -> None:
        """Start mission-owned work without blocking the manager scheduler."""

    def on_stop(self) -> None:
        """Release mission-owned resources; the method must be idempotent."""

    def on_pause(self) -> None:
        """Temporarily suspend work while retaining mission state."""

    def on_resume(self) -> None:
        """Resume work after a pause."""

    def on_tick(self, elapsed_seconds: float) -> None:
        """Process one scheduler tick."""


_TRANSITIONS: dict[MissionPhase, frozenset[MissionPhase]] = {
    MissionPhase.REGISTERED: frozenset(
        {MissionPhase.QUEUED, MissionPhase.STARTING, MissionPhase.CANCELLED}
    ),
    MissionPhase.QUEUED: frozenset(
        {
            MissionPhase.STARTING,
            MissionPhase.CANCELLED,
            MissionPhase.STOPPED,
            MissionPhase.FAILED,
        }
    ),
    MissionPhase.STARTING: frozenset(
        {MissionPhase.RUNNING, MissionPhase.FAILED, MissionPhase.CANCELLED}
    ),
    MissionPhase.RUNNING: frozenset(
        {
            MissionPhase.PAUSING,
            MissionPhase.STOPPING,
            MissionPhase.SUCCEEDED,
            MissionPhase.FAILED,
            MissionPhase.CANCELLED,
        }
    ),
    MissionPhase.PAUSING: frozenset(
        {MissionPhase.PAUSED, MissionPhase.FAILED, MissionPhase.CANCELLED}
    ),
    MissionPhase.PAUSED: frozenset(
        {
            MissionPhase.RUNNING,
            MissionPhase.STOPPING,
            MissionPhase.FAILED,
            MissionPhase.CANCELLED,
        }
    ),
    MissionPhase.STOPPING: frozenset(
        {MissionPhase.STOPPED, MissionPhase.FAILED, MissionPhase.CANCELLED}
    ),
    MissionPhase.STOPPED: frozenset(
        {MissionPhase.QUEUED, MissionPhase.STARTING, MissionPhase.CANCELLED}
    ),
    MissionPhase.SUCCEEDED: frozenset(
        {MissionPhase.QUEUED, MissionPhase.STARTING}
    ),
    MissionPhase.FAILED: frozenset(
        {MissionPhase.QUEUED, MissionPhase.STARTING, MissionPhase.CANCELLED}
    ),
    MissionPhase.CANCELLED: frozenset(
        {MissionPhase.QUEUED, MissionPhase.STARTING}
    ),
}


def ensure_mission_transition(previous: MissionPhase, current: MissionPhase) -> None:
    previous = MissionPhase(previous)
    current = MissionPhase(current)
    if current not in _TRANSITIONS[previous]:
        raise MissionTransitionError(
            f"Mission transition {previous.value} -> {current.value} is not allowed"
        )


def normalize_names(values: Any) -> frozenset[str]:
    normalized = {
        str(value).strip().lower()
        for value in values
        if str(value).strip()
    }
    return frozenset(normalized)


def normalize_mission_ids(values: Any) -> frozenset[str]:
    normalized = normalize_names(values)
    invalid = sorted(value for value in normalized if not _MISSION_ID_PATTERN.fullmatch(value))
    if invalid:
        raise ValueError(f"Invalid mission IDs: {', '.join(invalid)}")
    return normalized


def _normalize_optional_mission_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not _MISSION_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid mission ID: {value!r}")
    return normalized


__all__ = [
    "Mission",
    "MissionChain",
    "MissionChainSnapshot",
    "MissionConflictError",
    "MissionConflictPolicy",
    "MissionControl",
    "MissionDescriptor",
    "MissionError",
    "MissionEvent",
    "MissionEventLevel",
    "MissionEventQuery",
    "MissionEventType",
    "MissionManagerSnapshot",
    "MissionPermissionError",
    "MissionPhase",
    "MissionPrerequisitePolicy",
    "MissionPriority",
    "MissionRegistrationError",
    "MissionRetryPolicy",
    "MissionSnapshot",
    "MissionTransition",
    "MissionTransitionError",
    "ensure_mission_transition",
]
