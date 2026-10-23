"""Small shared registry for system and extension-provided wall clocks."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import math
from typing import Callable


class ClockQuality(str, Enum):
    """Freshness of the latest value supplied by a clock source."""

    LIVE = "live"
    ESTIMATED = "estimated"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ClockSample:
    """A clock value and the wall-clock instant when its source last updated it.

    ``estimated`` is set when ``value`` advances from a previously received
    sample rather than from a new source response. ``updated_at`` must describe
    that response, not the time this object was read.
    """

    value: datetime
    updated_at: datetime
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class ClockStatus:
    """Clock value with measurable source freshness for UI and consumers."""

    value: datetime | None
    updated_at: datetime | None
    age_seconds: float | None
    quality: ClockQuality


ClockProvider = Callable[[], ClockSample | datetime | None]

_MAX_FUTURE_UPDATE_SKEW_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class ClockReference:
    """Stable identity and display metadata for a clock supplied by an extension."""

    identifier: str
    title: str
    extension_id: str
    stale_after_seconds: float | None = None


class ClockReferenceRegistry:
    """Thread-safe registry shared through :class:`ExtensionContext`.

    Identifiers are derived from the owning extension and its stable local key;
    display titles are intentionally not used as persistent identity. Providers
    should return :class:`ClockSample` with aware timestamps. Legacy providers
    returning an aware :class:`datetime` remain usable, but their freshness is
    reported as unknown because they do not identify when their source updated.
    """

    _EXTENSION_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
    _KEY = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")

    def __init__(self) -> None:
        self._owner: str | None = None
        self._lock = threading.RLock()
        self._sources: dict[str, tuple[ClockReference, ClockProvider]] = {}

    def _scope(self, owner: str) -> ClockReferenceRegistry:
        if self._owner is not None:
            raise ValueError("A clock context cannot change owner")
        scoped = ClockReferenceRegistry()
        scoped._owner = owner
        scoped._lock = self._lock
        scoped._sources = self._sources
        return scoped

    def register(
        self,
        extension_id: str,
        key: str,
        title: str,
        provider: ClockProvider,
        *,
        stale_after_seconds: float | None = None,
    ) -> str:
        owner = str(extension_id).strip().lower()
        if self._owner is not None and owner != self._owner:
            raise ValueError("Clock source belongs to another extension")
        if owner == "system":
            raise ValueError("The system clock owner is reserved for the host")
        return self._register(
            owner,
            key,
            title,
            provider,
            stale_after_seconds=stale_after_seconds,
        )

    def publish(self, key: str, title: str, provider: ClockProvider, *,
                stale_after_seconds: float | None = None) -> str:
        """Publish a source owned by the extension context supplied by the host."""
        if self._owner is None:
            raise ValueError("Publishing requires an extension context")
        return self.register(self._owner, key, title, provider,
                             stale_after_seconds=stale_after_seconds)

    def _register_system(
        self,
        key: str,
        title: str,
        provider: ClockProvider,
        *,
        stale_after_seconds: float | None = None,
    ) -> str:
        """Register a host-owned source in the reserved ``system`` namespace."""
        if self._owner is not None:
            raise ValueError("System clocks require the host context")
        return self._register(
            "system",
            key,
            title,
            provider,
            stale_after_seconds=stale_after_seconds,
        )

    def _register(
        self,
        owner: str,
        key: str,
        title: str,
        provider: ClockProvider,
        *,
        stale_after_seconds: float | None = None,
    ) -> str:
        local_key = str(key).strip().lower()
        display_title = str(title).strip()
        if not self._EXTENSION_ID.fullmatch(owner):
            raise ValueError("Clock reference extension id is invalid")
        if not self._KEY.fullmatch(local_key):
            raise ValueError("Clock reference key is invalid")
        if not display_title:
            raise ValueError("Clock reference title cannot be empty")
        if not callable(provider):
            raise TypeError("Clock reference provider must be callable")
        if stale_after_seconds is not None:
            if isinstance(stale_after_seconds, bool):
                raise ValueError("Clock staleness limit must be a positive number")
            stale_after_seconds = float(stale_after_seconds)
            if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
                raise ValueError("Clock staleness limit must be a positive number")
        identifier = f"{owner}.{local_key}"
        reference = ClockReference(
            identifier, display_title, owner, stale_after_seconds
        )
        with self._lock:
            # Re-registering the same logical source updates its live provider
            # and label without changing the identifier stored in settings.
            self._sources[identifier] = (reference, provider)
        return identifier

    def unregister(self, identifier: str) -> bool:
        if self._owner is not None and not str(identifier).startswith(self._owner + "."):
            raise ValueError("Clock source belongs to another extension")
        if str(identifier).strip().lower().startswith("system."):
            raise ValueError("System clock references are owned by the host")
        with self._lock:
            return self._sources.pop(str(identifier), None) is not None

    def unregister_owner(self, extension_id: str) -> None:
        owner = str(extension_id).strip().lower()
        if self._owner is not None and owner != self._owner:
            raise ValueError("Clock source belongs to another extension")
        if owner == "system":
            raise ValueError("System clock references are owned by the host")
        prefix = owner + "."
        with self._lock:
            for identifier in tuple(self._sources):
                if identifier.startswith(prefix):
                    del self._sources[identifier]

    def references(self) -> tuple[ClockReference, ...]:
        with self._lock:
            return tuple(
                self._sources[identifier][0] for identifier in sorted(self._sources)
            )

    @staticmethod
    def _is_aware(value: object) -> bool:
        return (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
        )

    def status(self, identifier: str) -> ClockStatus:
        """Return the current value and its age relative to the system clock.

        A source can declare its established stale threshold at registration.
        Without one, age is still reported, but freshness remains ``UNKNOWN``;
        the registry does not guess a threshold. Legacy datetime-only providers
        also report ``UNKNOWN`` quality because they carry no update timestamp.
        """
        with self._lock:
            entry = self._sources.get(str(identifier))
        if entry is None:
            return ClockStatus(None, None, None, ClockQuality.UNAVAILABLE)
        try:
            sample = entry[1]()
            if isinstance(sample, datetime):
                if self._is_aware(sample):
                    return ClockStatus(
                        sample, None, None, ClockQuality.UNKNOWN
                    )
                return ClockStatus(None, None, None, ClockQuality.UNAVAILABLE)
            if (
                not isinstance(sample, ClockSample)
                or not self._is_aware(sample.value)
                or not self._is_aware(sample.updated_at)
            ):
                return ClockStatus(None, None, None, ClockQuality.UNAVAILABLE)

            skew_seconds = (
                datetime.now(UTC) - sample.updated_at.astimezone(UTC)
            ).total_seconds()
            if skew_seconds < -_MAX_FUTURE_UPDATE_SKEW_SECONDS:
                return ClockStatus(
                    sample.value,
                    sample.updated_at,
                    None,
                    ClockQuality.UNKNOWN,
                )
            age_seconds = max(0.0, skew_seconds)
            stale_after = entry[0].stale_after_seconds
            if stale_after is not None and age_seconds > stale_after:
                quality = ClockQuality.STALE
            elif sample.estimated:
                quality = ClockQuality.ESTIMATED
            elif stale_after is None:
                quality = ClockQuality.UNKNOWN
            else:
                quality = ClockQuality.LIVE
            return ClockStatus(
                sample.value,
                sample.updated_at,
                age_seconds,
                quality,
            )
        except Exception:
            # A provider can temporarily be unavailable (for example, before
            # a server has supplied its first clock sample).
            return ClockStatus(None, None, None, ClockQuality.UNAVAILABLE)

    def current_time(self, identifier: str) -> datetime | None:
        """Return a usable time, preserving the legacy ``datetime | None`` API."""
        status = self.status(identifier)
        if status.quality in {ClockQuality.UNAVAILABLE, ClockQuality.STALE}:
            return None
        return status.value
