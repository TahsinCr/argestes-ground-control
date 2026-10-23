"""Small shared registry for extension-provided wall-clock references."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable


ClockProvider = Callable[[], datetime | None]


@dataclass(frozen=True, slots=True)
class ClockReference:
    """Stable identity and display metadata for a clock supplied by an extension."""

    identifier: str
    title: str
    extension_id: str


class ClockReferenceRegistry:
    """Thread-safe registry shared through :class:`ExtensionContext`.

    Identifiers are derived from the owning extension and its stable local key;
    display titles are intentionally not used as persistent identity.
    """

    _EXTENSION_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
    _KEY = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sources: dict[str, tuple[ClockReference, ClockProvider]] = {}

    def register(
        self,
        extension_id: str,
        key: str,
        title: str,
        provider: ClockProvider,
    ) -> str:
        owner = str(extension_id).strip().lower()
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
        identifier = f"{owner}.{local_key}"
        reference = ClockReference(identifier, display_title, owner)
        with self._lock:
            # Re-registering the same logical source updates its live provider
            # and label without changing the identifier stored in settings.
            self._sources[identifier] = (reference, provider)
        return identifier

    def unregister(self, identifier: str) -> bool:
        with self._lock:
            return self._sources.pop(str(identifier), None) is not None

    def unregister_owner(self, extension_id: str) -> None:
        owner = str(extension_id).strip().lower()
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

    def current_time(self, identifier: str) -> datetime | None:
        with self._lock:
            entry = self._sources.get(str(identifier))
        if entry is None:
            return None
        try:
            value = entry[1]()
            if not isinstance(value, datetime) or value.tzinfo is None:
                return None
            if value.utcoffset() is None:
                return None
            return value.astimezone(UTC)
        except Exception:
            # A provider can temporarily be unavailable (for example, before
            # a server has supplied its first clock sample).
            return None
