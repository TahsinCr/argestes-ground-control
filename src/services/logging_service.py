from __future__ import annotations

import logging
from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from src.models.logs import LogEntry


class ServiceLogger:
    def __init__(self, service: "LoggingService", source: str) -> None:
        self._service = service
        self._source = source

    def emit(self, severity: str, message: str) -> None:
        self._service.log(self._source, message, severity)

    def debug(self, message: str) -> None:
        self.emit("info", message)

    def info(self, message: str) -> None:
        self.emit("info", message)

    def success(self, message: str) -> None:
        self.emit("success", message)

    def warning(self, message: str) -> None:
        self.emit("warning", message)

    def error(self, message: str) -> None:
        self.emit("danger", message)


class QtLogHandler(logging.Handler):
    def __init__(self, log_service: "LoggingService", source: str = "PYLOG") -> None:
        super().__init__()
        self._log_service = log_service
        self._source = source

    def emit(self, record: logging.LogRecord) -> None:
        severity = {
            logging.DEBUG: "info",
            logging.INFO: "info",
            logging.WARNING: "warning",
            logging.ERROR: "danger",
            logging.CRITICAL: "danger",
        }.get(record.levelno, "info")
        self._log_service.log(self._source, self.format(record), severity)


class LoggingService(QObject):
    entry_emitted = pyqtSignal(object)

    def __init__(self, max_entries: int = 1000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._history: list[LogEntry] = []
        self._max_entries = max_entries

    @property
    def history(self) -> list[LogEntry]:
        return list(self._history)

    def child(self, source: str) -> ServiceLogger:
        return ServiceLogger(self, source)

    def log(self, source: str, message: str, severity: str = "info") -> None:
        self.emit_entry(LogEntry(source=source, message=message, severity=severity))

    def emit_entry(self, entry: LogEntry) -> None:
        self._history.append(entry)
        if len(self._history) > self._max_entries:
            self._history = self._history[-self._max_entries :]
        self.entry_emitted.emit(entry)

    def reemit_history(self) -> None:
        for entry in self._history:
            self.entry_emitted.emit(replace(entry))

    def create_python_handler(self, source: str = "PYLOG") -> QtLogHandler:
        return QtLogHandler(self, source)
