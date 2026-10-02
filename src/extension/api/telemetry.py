"""Optional processing of normalized data, independent of transport and UI."""

from collections.abc import Callable
from typing import Protocol
from src.models.telemetry import TelemetrySnapshot


class TelemetryPipeline(Protocol):
    def add_processor(
        self, key: str, processor: Callable[[TelemetrySnapshot], TelemetrySnapshot]
    ) -> None: ...
    def remove_processor(self, key: str) -> None: ...
