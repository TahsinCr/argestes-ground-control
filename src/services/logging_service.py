from __future__ import annotations

import logging
import json
import os
import threading
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.core.events import EventBus
from src.models.logs import LogEntry
from src.services.abstracts.logging import LogService


class ServiceLogger:
    def __init__(self, service: "LoggingService", source: str) -> None:
        self._service = service
        self._source = source

    def emit(self, severity: str, message: str) -> None:
        self._service.log(self._source, message, severity)

    def event(
        self,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, Any] | None = None,
        device_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._service.event(
            self._source,
            action,
            message,
            severity=severity,
            details=details,
            device_id=device_id,
            correlation_id=correlation_id,
        )

    def debug(self, message: str) -> None: self.emit("debug", message)
    def info(self, message: str) -> None: self.emit("info", message)
    def success(self, message: str) -> None: self.emit("success", message)
    def warning(self, message: str) -> None: self.emit("warning", message)
    def error(self, message: str) -> None: self.emit("danger", message)


class ServiceLogHandler(logging.Handler):
    def __init__(self, log_service: "LoggingService", source: str = "PYLOG") -> None:
        super().__init__()
        self._log_service = log_service
        self._source = source

    def emit(self, record: logging.LogRecord) -> None:
        severity = {
            logging.DEBUG: "debug",
            logging.INFO: "info",
            logging.WARNING: "warning",
            logging.ERROR: "danger",
            logging.CRITICAL: "danger",
        }.get(record.levelno, "info")
        service = self._log_service
        parts = Path(record.pathname).parts
        if "extensions" in parts:
            index = parts.index("extensions") + 1
            if index < len(parts):
                service = service.for_extension(parts[index])
        elif record.name.startswith("extensions."):
            service = service.for_extension(record.name.split(".")[1])
        service.log(self._source, self.format(record), severity)


class LoggingService(LogService):
    """Central structured logging service consumable by multiple interfaces."""

    def __init__(
        self,
        max_entries: int = 1000,
        *,
        log_path: Path | None = None,
        max_file_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries pozitif olmalı")
        if max_file_bytes <= 0 or backup_count < 0:
            raise ValueError("Log dosyası limitleri geçersiz")
        self._entries = EventBus[LogEntry]()
        self._history: deque[LogEntry] = deque(maxlen=max_entries)
        self._lock = threading.RLock()
        self._log_path = Path(log_path) if log_path is not None else None
        self._max_file_bytes = int(max_file_bytes)
        self._backup_count = int(backup_count)
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def entries(self) -> EventBus[LogEntry]:
        return self._entries

    @property
    def history(self) -> list[LogEntry]:
        with self._lock:
            return [replace(item, details=dict(item.details)) for item in self._history]

    def child(self, source: str) -> ServiceLogger:
        return ServiceLogger(self, source)

    def for_extension(self, identifier: str) -> "LoggingService":
        return ExtensionLoggingService(self, identifier)

    def log(self, source: str, message: str, severity: str = "info") -> None:
        self.emit_entry(LogEntry(source=source, message=message, severity=severity))

    def event(
        self,
        source: str,
        action: str,
        message: str,
        *,
        severity: str = "info",
        details: dict[str, Any] | None = None,
        device_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.emit_entry(
            LogEntry(
                source=source,
                action=action,
                message=message,
                severity=severity,
                details=details or {},
                device_id=device_id,
                correlation_id=correlation_id,
            )
        )

    def emit_entry(self, entry: LogEntry) -> None:
        self.emit_entries((entry,))

    def emit_entries(self, entries: tuple[LogEntry, ...] | list[LogEntry]) -> None:
        snapshots = tuple(replace(entry, details=dict(entry.details)) for entry in entries)
        if not snapshots:
            return
        with self._lock:
            self._history.extend(snapshots)
            self._persist_many(snapshots)
        for snapshot in snapshots:
            self._entries.publish(replace(snapshot, details=dict(snapshot.details)))

    def reemit_history(self) -> None:
        for entry in self.history:
            self._entries.publish(entry)

    def create_python_handler(self, source: str = "PYLOG") -> ServiceLogHandler:
        return ServiceLogHandler(self, source)

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    def flush(self) -> None:
        if self._log_path is None or not self._log_path.exists():
            return
        with self._lock:
            try:
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                return

    def _persist(self, entry: LogEntry) -> None:
        self._persist_many((entry,))

    def _persist_many(self, entries: tuple[LogEntry, ...]) -> None:
        if self._log_path is None:
            return
        payloads = tuple(
            json.dumps(entry.to_dict(), ensure_ascii=False, default=str, separators=(",", ":"))
            for entry in entries
        )
        encoded_size = sum(len(payload.encode("utf-8")) + 1 for payload in payloads)
        if self._log_path.exists() and self._log_path.stat().st_size + encoded_size > self._max_file_bytes:
            self._rotate_files()
        # Closing the handle makes each record immediately visible while the
        # kernel performs physical disk writeback. A synchronous fsync here
        # used to block whichever thread emitted the log, including Qt's GUI
        # thread, at roughly one-second intervals. Runtime shutdown calls
        # flush() once to provide the durability boundary without an idle
        # writer thread or periodic UI stalls.
        if self._log_path.suffix == ".json":
            # Maintain a valid array without loading the session into memory.
            exists = self._log_path.exists() and self._log_path.stat().st_size > 2
            mode = "r+b" if self._log_path.exists() else "w+b"
            with self._log_path.open(mode) as handle:
                if exists:
                    handle.seek(-1, os.SEEK_END)
                    prefix = b",\n"
                else:
                    handle.seek(0)
                    prefix = b"["
                handle.write(prefix + ",\n".join(payloads).encode("utf-8") + b"]")
                handle.truncate()
        else:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(payloads))
                handle.write("\n")

    def _rotate_files(self) -> None:
        if self._log_path is None:
            return
        if self._backup_count == 0:
            self._log_path.unlink(missing_ok=True)
            return
        oldest = self._log_path.with_name(f"{self._log_path.name}.{self._backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self._backup_count - 1, 0, -1):
            source = self._log_path.with_name(f"{self._log_path.name}.{index}")
            if source.exists():
                source.replace(self._log_path.with_name(f"{self._log_path.name}.{index + 1}"))
        if self._log_path.exists():
            self._log_path.replace(self._log_path.with_name(f"{self._log_path.name}.1"))


class ExtensionLoggingService(LoggingService):
    """The host attaches origin even when extension code only uses child()."""

    def __init__(self, service: LoggingService, identifier: str):
        self._service = service
        self._identifier = identifier

    def __getattr__(self, name):
        return getattr(self._service, name)

    def for_extension(self, identifier):
        return self._service.for_extension(identifier)

    def emit_entries(self, entries):
        self._service.emit_entries(tuple(replace(entry, extension_id=self._identifier) for entry in entries))


QtLogHandler = ServiceLogHandler
