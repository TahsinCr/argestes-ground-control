from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


class LogStoragePage(QWidget):
    """Inspect and explicitly manage archived application session logs."""

    def __init__(self, logging_service, language: str, parent=None) -> None:
        super().__init__(parent)
        self.logging_service = logging_service
        self.language = language
        self._export_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="LogExport"
        )
        self._export_future: Future | None = None
        self._export_name = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        heading = QLabel(self._text("title"), self)
        heading.setObjectName("sectionTitle")
        description = QLabel(self._text("description"), self)
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

        summary = QGroupBox(self._text("summary"), self)
        summary_layout = QHBoxLayout(summary)
        self.file_count = QLabel("—", summary)
        self.total_size = QLabel("—", summary)
        self.archived_count = QLabel("—", summary)
        for label in (self.file_count, self.total_size, self.archived_count):
            summary_layout.addWidget(label, 1)
        layout.addWidget(summary)

        files_box = QGroupBox(self._text("files"), self)
        files_layout = QVBoxLayout(files_box)
        self.files = QListWidget(files_box)
        self.files.currentItemChanged.connect(self._selection_changed)
        files_layout.addWidget(self.files)
        layout.addWidget(files_box, 1)

        self.status = QLabel(self._text("retention"), self)
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton(self._text("refresh"), self)
        self.export_button = QPushButton(self._text("export"), self)
        self.delete_button = QPushButton(self._text("delete"), self)
        self.delete_button.setObjectName("dangerButton")
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self._export_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        self._export_timer = QTimer(self)
        self._export_timer.setInterval(100)
        self._export_timer.timeout.connect(self._finish_export)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        actions.addWidget(self.export_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.refresh()

    def _text(self, key: str, **values) -> str:
        return t(f"settings.logs.{key}", self.language, **values)

    def refresh(self) -> None:
        summary = self.logging_service.storage_summary()
        files = self.logging_service.storage_files()
        self.file_count.setText(self._text("count", count=summary["file_count"]))
        self.total_size.setText(
            self._text("size", size=_format_bytes(summary["total_bytes"]))
        )
        self.archived_count.setText(
            self._text("archived", count=summary["archived_count"])
        )
        self.files.clear()
        active_item = None
        for file in files:
            marker = self._text("active_marker") if file["active"] else ""
            item = QListWidgetItem(
                f"{file['name']}  ·  {_format_bytes(file['size_bytes'])}  {marker}"
            )
            item.setData(Qt.ItemDataRole.UserRole, file["name"])
            item.setData(Qt.ItemDataRole.UserRole + 1, file["active"])
            self.files.addItem(item)
            if file["active"]:
                active_item = item
        if active_item is not None:
            self.files.setCurrentItem(active_item)
        self._selection_changed(self.files.currentItem(), None)

    def _selection_changed(self, current, _previous) -> None:
        selected = current is not None
        active = bool(current.data(Qt.ItemDataRole.UserRole + 1)) if selected else False
        busy = self._export_future is not None
        self.refresh_button.setEnabled(not busy)
        self.export_button.setEnabled(selected and not busy)
        self.delete_button.setEnabled(selected and not active and not busy)

    def _export_selected(self) -> None:
        item = self.files.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        destination, _filter = QFileDialog.getSaveFileName(
            self,
            self._text("export"),
            name,
            "JSON (*.json)",
        )
        if not destination:
            return
        target = Path(destination)
        if target.exists():
            answer = QMessageBox.question(
                self,
                self._text("export"),
                self._text("overwrite"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._export_name = name
        self._export_future = self._export_executor.submit(
            self.logging_service.export_log, name, target
        )
        self.status.setText(self._text("exporting"))
        self._selection_changed(item, None)
        self._export_timer.start()

    def _finish_export(self) -> None:
        future = self._export_future
        if future is None or not future.done():
            return
        self._export_timer.stop()
        self._export_future = None
        try:
            future.result()
            self.status.setText(self._text("exported", name=self._export_name))
        except Exception as error:
            self.status.setText(self._text("failed", error=str(error)))
        finally:
            self._selection_changed(self.files.currentItem(), None)

    def closeEvent(self, event) -> None:
        self._export_timer.stop()
        self._export_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def _delete_selected(self) -> None:
        item = self.files.currentItem()
        if item is None or bool(item.data(Qt.ItemDataRole.UserRole + 1)):
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.warning(
            self,
            self._text("delete"),
            self._text("delete_confirm", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.logging_service.delete_archived_log(name)
            self.status.setText(self._text("deleted", name=name))
            self.refresh()
        except (OSError, ValueError) as error:
            self.status.setText(self._text("failed", error=str(error)))
