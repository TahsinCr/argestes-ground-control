from __future__ import annotations

from abc import abstractmethod
from datetime import datetime

from src.core.abstracts import Service
from src.core.events import EventStream
from src.models.competition import KamikazeQrWorkflowState


class KamikazeQrService(Service):
    """Kamikaze komutu, QR işleme ve sunucu gönderimini birleştiren port."""

    @property
    @abstractmethod
    def state(self) -> KamikazeQrWorkflowState: ...

    @property
    @abstractmethod
    def changed(self) -> EventStream[KamikazeQrWorkflowState]: ...

    @abstractmethod
    def activate(self, *, started_at: datetime | None = None) -> None: ...

    @abstractmethod
    def deactivate(self) -> None: ...
