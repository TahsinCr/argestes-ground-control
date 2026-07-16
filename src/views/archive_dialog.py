from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.config.i18n import t
from src.services.mission_archive import MissionArchiveService
from src.themes.style import ThemePalette
from src.widgets.title_bar import AppTitleBar


class MissionArchiveDialog(QDialog):
    def __init__(
        self,
        *,
        archive_service: MissionArchiveService,
        language: str,
        theme: ThemePalette,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._archive_service = archive_service
        self._language = language
        self._selected_path: Path | None = None
        self._selected_action: str | None = None
        self.setObjectName("FramelessDialog")
        self.setWindowTitle(t("dialog.archive.title", language))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.resize(620, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = AppTitleBar(self, theme, self)
        self.title_bar.set_title(t("dialog.archive.title", language))
        root.addWidget(self.title_bar)

        shell = QWidget(self)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(10, 10, 10, 10)
        shell_layout.setSpacing(8)

        note = QLabel(t("dialog.archive.note", language), self)
        note.setProperty("role", "readout-label")
        note.setWordWrap(True)
        shell_layout.addWidget(note)

        self.name_input = QLineEdit(self)
        self.name_input.setPlaceholderText(t("dialog.archive.name_placeholder", language))
        self.name_input.setText(self._default_name())
        self.name_input.textChanged.connect(self._sync_buttons)
        shell_layout.addWidget(self.name_input)

        self.file_list = QListWidget(self)
        self.file_list.setObjectName("ArchiveList")
        self.file_list.itemSelectionChanged.connect(self._sync_buttons)
        self.file_list.itemDoubleClicked.connect(lambda _: self._accept_load())
        shell_layout.addWidget(self.file_list, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel("", self)
        self.status_label.setProperty("role", "readout-label")
        footer.addWidget(self.status_label, 1)

        self.cancel_button = QPushButton(t("dialog.settings.cancel", language), self)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)

        self.load_button = QPushButton(t("dialog.archive.load_action", language), self)
        self.load_button.setProperty("role", "primary")
        self.load_button.clicked.connect(self._accept_load)
        footer.addWidget(self.load_button)

        self.save_button = QPushButton(t("dialog.archive.save_action", language), self)
        self.save_button.setProperty("role", "success")
        self.save_button.clicked.connect(self._accept_save)
        footer.addWidget(self.save_button)
        shell_layout.addLayout(footer)

        root.addWidget(shell, 1)
        self._populate()
        self._sync_buttons()

    @property
    def selected_path(self) -> Path | None:
        return self._selected_path

    @property
    def selected_action(self) -> str | None:
        return self._selected_action

    def _default_name(self) -> str:
        return f"mission-plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    def _populate(self) -> None:
        self.file_list.clear()
        plans = self._archive_service.list_plans()
        for plan in plans:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(plan))
            row = self._build_row(plan)
            item.setSizeHint(row.sizeHint())
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, row)
        if plans:
            self.file_list.setCurrentRow(0)
        self.status_label.setText(
            t("dialog.archive.count", self._language).format(count=len(plans))
        )

    def _build_row(self, plan: Path) -> QWidget:
        row = QWidget(self.file_list)
        row.setObjectName("ArchiveRow")
        row.setProperty("archive_path", str(plan))
        row.installEventFilter(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 5, 6, 5)
        layout.setSpacing(8)

        label = QLabel(plan.name, row)
        label.setProperty("role", "archive-name")
        label.setProperty("archive_path", str(plan))
        label.installEventFilter(self)
        layout.addWidget(label, 1)

        delete_button = QPushButton("×", row)
        delete_button.setObjectName("ArchiveDeleteButton")
        delete_button.setToolTip(t("dialog.archive.delete_action", self._language))
        delete_button.setProperty("role", "danger")
        delete_button.setProperty("compact", "true")
        delete_button.setFixedSize(28, 24)
        delete_button.clicked.connect(lambda _checked=False, path=plan: self._delete_plan(path))
        layout.addWidget(delete_button)
        return row

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            path = watched.property("archive_path")
            if path:
                self._select_path(Path(path))
        return super().eventFilter(watched, event)

    def _selected_list_path(self) -> Path | None:
        item = self.file_list.currentItem()
        if not item:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def _select_path(self, path: Path) -> None:
        target = str(path)
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == target:
                self.file_list.setCurrentItem(item)
                return

    def _save_path(self) -> Path | None:
        name = self.name_input.text().strip()
        if not name:
            return None
        path = self._archive_service.directory / name
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        return path

    def _delete_plan(self, path: Path) -> None:
        self._archive_service.delete_plan(path)
        self._populate()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.load_button.setEnabled(self._selected_list_path() is not None)
        self.save_button.setEnabled(self._save_path() is not None)

    def _accept_load(self) -> None:
        path = self._selected_list_path()
        if path is None:
            return
        self._selected_path = path
        self._selected_action = "load"
        self.accept()

    def _accept_save(self) -> None:
        path = self._save_path()
        if path is None:
            return
        self._selected_path = path
        self._selected_action = "save"
        self.accept()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.sync_window_state()
