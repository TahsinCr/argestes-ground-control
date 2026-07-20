from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.mission import MapContact, MapOverlay, MissionState


class MissionService(Service):
    @property
    @abstractmethod
    def state(self) -> MissionState: ...

    @property
    @abstractmethod
    def changed(self) -> EventStream[MissionState]: ...

    @abstractmethod
    def upsert_overlay(self, overlay: MapOverlay) -> None: ...

    @abstractmethod
    def remove_overlay(self, overlay_id: str) -> None: ...

    @abstractmethod
    def set_overlays(self, overlays: Iterable[MapOverlay]) -> None: ...

    @abstractmethod
    def replace_source_overlays(
        self,
        source: str,
        overlays: Iterable[MapOverlay],
    ) -> None:
        """Bir backend kaynağına ait harita öğelerini atomik olarak yeniler."""

    @abstractmethod
    def upsert_contact(self, contact: MapContact) -> None: ...

    @abstractmethod
    def replace_contacts(
        self,
        contacts: Iterable[MapContact],
        *,
        source: str | None = None,
    ) -> None:
        """Bir kaynağa ait temasları tek bir atomik durum değişikliğiyle yeniler."""

    @abstractmethod
    def remove_contact(self, contact_id: str) -> None: ...

    @abstractmethod
    def clear_contacts(self) -> None: ...

    @abstractmethod
    def clear_dynamic_overlays(self) -> None: ...

    @abstractmethod
    def set_base_layer(self, layer: str) -> None: ...

    @abstractmethod
    def set_active_tool(self, tool: str) -> None: ...

    @abstractmethod
    def select_overlay(self, overlay_id: str | None) -> None: ...

    @abstractmethod
    def load_state(self, state: MissionState) -> None: ...
