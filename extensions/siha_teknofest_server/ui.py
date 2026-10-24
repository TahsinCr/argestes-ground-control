"""Archived profile cards and an independent profile editor."""

import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.services.settings import SettingsService
from src.ui.i18n import t
from src.ui.themes.palette import palette_for
from src.ui.widgets.anchored_popup import open_above_anchor
from src.ui.widgets.common import StatusChip

from .connections_popup import CompetitionServersPopup
from .models import CompetitionServerConfig
from .runtime import CompetitionRuntime


def _format_bytes(value):
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


class ServerPanel(QWidget):
    def __init__(self, context, parent=None, *, popup_mode=False):
        super().__init__(parent)
        self.settings = context.services.resolve(SettingsService)
        self.runtime = context.services.resolve(CompetitionRuntime)
        self.language = self.settings.current.ui.language
        self.popup_mode, self.pending, self.anchor = popup_mode, None, None
        self.profiles, self._signature = [], None
        self._history_executor = (
            None
            if popup_mode
            else ThreadPoolExecutor(max_workers=1, thread_name_prefix="CompetitionHistory")
        )
        self._history_future: Future | None = None
        self._history_stats_future: Future | None = None
        self._history_last_refresh = 0.0
        self._history_status = ""
        self.popup = CompetitionServersPopup(
            palette_for(self.settings.current.ui.theme), self, language=self.language
        )
        self.popup.server_edit_requested.connect(self.edit)
        self.popup.servers_saved.connect(self.save_profiles)
        self.popup.server_connect_requested.connect(self.connect_profile)
        if not popup_mode:
            self.popup.setWindowFlags(Qt.WindowType.Widget)
            self.popup.setMinimumSize(0, 0)
            self.popup.setMaximumSize(16777215, 16777215)
            self.popup.back_button.hide()
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.tabs = QTabWidget(self)
            self.tabs.addTab(self.popup, t("teknofest.servers_tab", self.language))
            self.history_page = self._build_history_page()
            self.tabs.addTab(self.history_page, t("teknofest.history_tab", self.language))
            layout.addWidget(self.tabs)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self._finish_history_operation()
        self._finish_history_stats()
        if self.pending is not None and self.pending.done():
            error = self.pending.exception()
            self.pending = None
            if error:
                QMessageBox.warning(self, "TEKNOFEST", str(error))
        profiles = tuple(
            CompetitionServerConfig(
                **{key: value for key, value in item.items() if key != "password"}
            )
            for item in self.settings.extension_config("siha_teknofest_server").get(
                "servers", []
            )
        )
        states = {
            key: (session.connected, session.running, session.error)
            for key, session in self.runtime.sessions.copy().items()
        }
        signature = (profiles, tuple(states.items()), self.pending is None)
        if signature != self._signature:
            self._signature, self.profiles = signature, list(profiles)
            self.popup._server_states = states
            self.popup.show_servers(
                profiles,
                active_identifier="",
                connected=False,
                running=False,
                available=self.pending is None,
            )
        if (
            not self.popup_mode
            and self._history_future is None
            and self._history_stats_future is None
            and time.monotonic() - self._history_last_refresh >= 2.0
        ):
            self._refresh_history_stats()

    def _build_history_page(self):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        heading = QLabel(t("teknofest.history_title", self.language), page)
        heading.setObjectName("sectionTitle")
        description = QLabel(t("teknofest.history_description", self.language), page)
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

        summary = QGroupBox(t("teknofest.history_storage", self.language), page)
        form = QFormLayout(summary)
        self.history_count = QLabel("—", summary)
        self.history_size = QLabel("—", summary)
        self.history_range = QLabel("—", summary)
        self.history_path = QLabel(self.runtime.database.path.name, summary)
        for label in (self.history_path,):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(t("teknofest.history_records", self.language), self.history_count)
        form.addRow(t("teknofest.history_size", self.language), self.history_size)
        form.addRow(t("teknofest.history_range", self.language), self.history_range)
        form.addRow(t("teknofest.history_file", self.language), self.history_path)
        layout.addWidget(summary)

        note = QLabel(t("teknofest.history_retention_note", self.language), page)
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        self.history_status = QLabel("", page)
        self.history_status.setObjectName("mutedLabel")
        layout.addWidget(self.history_status)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.history_export = QPushButton(t("teknofest.history_export", self.language), page)
        self.history_clear = QPushButton(t("teknofest.history_clear", self.language), page)
        self.history_clear.setObjectName("dangerButton")
        self.history_export.clicked.connect(self._export_history)
        self.history_clear.clicked.connect(self._clear_history)
        actions.addWidget(self.history_export)
        actions.addWidget(self.history_clear)
        layout.addLayout(actions)
        return page

    def _refresh_history_stats(self):
        self._history_last_refresh = time.monotonic()
        if self._history_executor is None or self._history_stats_future is not None:
            return
        self._history_stats_future = self._history_executor.submit(
            self.runtime.database.storage_summary
        )

    def _finish_history_stats(self):
        future = self._history_stats_future
        if future is None or not future.done():
            return
        self._history_stats_future = None
        try:
            summary = future.result()
        except Exception as error:
            self.history_status.setText(str(error))
            return
        self.history_count.setText(str(summary["records"]))
        self.history_size.setText(_format_bytes(summary["size_bytes"]))
        oldest, newest = summary["oldest"], summary["newest"]
        self.history_range.setText(
            f"{oldest or '—'}  →  {newest or '—'}"
        )
        self.history_status.setText(self._history_status)

    def _finish_history_operation(self):
        future = self._history_future
        if future is None or not future.done():
            return
        self._history_future = None
        try:
            result = future.result()
            self._history_status = (
                t("teknofest.history_exported", self.language, count=result)
                if isinstance(result, int)
                else t("teknofest.history_cleared", self.language, count=result["removed"])
            )
        except Exception as error:
            self._history_status = t(
                "teknofest.history_operation_failed", self.language, error=str(error)
            )
        self.history_export.setEnabled(True)
        self.history_clear.setEnabled(True)
        self._history_last_refresh = 0.0

    def _export_history(self):
        if self._history_future is not None:
            return
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self,
            t("teknofest.history_export", self.language),
            "teknofest-history.jsonl",
            "JSON Lines (*.jsonl)",
        )
        if not destination:
            return
        destination = Path(destination)
        if destination.suffix.lower() != ".jsonl":
            destination = destination.with_suffix(".jsonl")
        if destination.exists():
            answer = QMessageBox.question(
                self,
                t("teknofest.history_export", self.language),
                t("teknofest.history_overwrite", self.language),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.history_export.setEnabled(False)
        self.history_clear.setEnabled(False)
        self.history_status.setText(t("teknofest.history_working", self.language))
        self._history_future = self._history_executor.submit(
            self.runtime.database.export_jsonl, destination
        )

    def _clear_history(self):
        if self._history_future is not None:
            return
        answer = QMessageBox.warning(
            self,
            t("teknofest.history_clear", self.language),
            t("teknofest.history_clear_confirm", self.language),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.history_export.setEnabled(False)
        self.history_clear.setEnabled(False)
        self.history_status.setText(t("teknofest.history_working", self.language))
        self._history_future = self._history_executor.submit(
            lambda: {"removed": self.runtime.database.clear_history()}
        )

    def save_profiles(self, profiles):
        try:
            self.pending = self.runtime.configure(
                [item.to_dict(include_password=True) for item in profiles]
            )
            self.refresh()
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "TEKNOFEST", str(error))

    def connect_profile(self, profile):
        session = self.runtime.sessions.get(profile.identifier)
        if session is None or self.pending is not None:
            return
        if session.running:
            self.pending = self.runtime.disconnect(profile.identifier)
        else:
            self.pending = self.runtime.connect(profile.identifier)
        self.refresh()

    def open(self, anchor):
        self.anchor = anchor
        self.refresh()
        open_above_anchor(self.popup, anchor)
        self.timer.start()

    def edit(self, profile=None):
        is_new = profile is None
        profile = profile or CompetitionServerConfig(name="TEKNOFEST")
        dialog = QDialog(self)
        dialog.setWindowTitle(t("teknofest.edit", self.language))
        dialog.resize(480, 360)
        root = QVBoxLayout(dialog)
        box = QGroupBox(
            t("extension.siha_teknofest_server.name", self.language), dialog
        )
        form = QFormLayout(box)
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        controls = {}
        for key in ("name", "username"):
            field = QLineEdit(str(getattr(profile, key)), box)
            controls[key] = field
            form.addRow(t("teknofest." + key, self.language), field)
        scheme = QComboBox(box)
        scheme.addItem("HTTPS", "https")
        scheme.addItem("HTTP", "http")
        selected_scheme = urlsplit(profile.base_url).scheme.lower()
        scheme.setCurrentIndex(max(0, scheme.findData(selected_scheme)))
        address = QLineEdit(profile.base_url.split("://", 1)[-1], box)
        address_row = QWidget(box)
        address_layout = QHBoxLayout(address_row)
        address_layout.setContentsMargins(0, 0, 0, 0)
        address_layout.addWidget(scheme)
        address_layout.addWidget(address, 1)
        controls["base_url"] = address
        form.addRow(t("teknofest.base_url", self.language), address_row)
        password = QLineEdit(box)
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText(
            t("teknofest.password.keep" if not is_new else "teknofest.password.required", self.language)
        )
        controls["password"] = password
        form.addRow(t("teknofest.password", self.language), password)
        transport_note = QLabel(t("teknofest.transport_note", self.language), box)
        transport_note.setWordWrap(True)
        transport_note.setObjectName("mutedLabel")
        form.addRow(transport_note)
        team, timeout = QSpinBox(box), QDoubleSpinBox(box)
        team.setRange(1, 9999)
        team.setValue(profile.team_number)
        timeout.setRange(0.1, 15)
        timeout.setValue(profile.timeout_s)
        timeout.setSuffix(" s")
        auto = QCheckBox(t("teknofest.auto", self.language), box)
        auto.setChecked(profile.auto_connect)
        form.addRow(t("teknofest.team", self.language), team)
        form.addRow(t("teknofest.timeout", self.language), timeout)
        form.addRow(auto)
        root.addWidget(box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        root.addWidget(buttons)
        result = []

        def accept():
            try:
                password_value = controls["password"].text()
                if is_new and not password_value:
                    raise ValueError(t("teknofest.password.required", self.language))
                host = controls["base_url"].text().strip()
                if host.startswith(("http://", "https://")):
                    host = host.split("://", 1)[1]
                result.append(
                    CompetitionServerConfig(
                        identifier=profile.identifier,
                        base_url=f"{scheme.currentData()}://{host}",
                        team_number=team.value(),
                        timeout_s=timeout.value(),
                        auto_connect=auto.isChecked(),
                        name=controls["name"].text().strip(),
                        username=controls["username"].text().strip(),
                        password=password_value,
                    )
                )
            except ValueError as error:
                QMessageBox.warning(dialog, "TEKNOFEST", str(error))
                return
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = result[0]
            profiles = [
                updated if item.identifier == updated.identifier else item
                for item in self.profiles
            ]
            if not any(item.identifier == updated.identifier for item in self.profiles):
                profiles.append(updated)
            self.save_profiles(profiles)
        dialog.deleteLater()
        if self.popup_mode and self.anchor is not None:
            self.open(self.anchor)

    def set_theme(self, theme):
        self.popup.set_theme(theme)

    def set_language(self, language):
        self.language = language
        self.popup.set_language(language)
        if not self.popup_mode:
            self.tabs.setTabText(0, t("teknofest.servers_tab", language))
            self.tabs.setTabText(1, t("teknofest.history_tab", language))

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def prepare_shutdown(self):
        self.timer.stop()
        if self._history_executor is not None:
            self._history_executor.shutdown(wait=False, cancel_futures=True)
        self.popup.close()


class ServerStatusWidget(QWidget):
    def __init__(self, context, parent, theme, language):
        super().__init__(parent)
        self.context, self.theme, self.language = context, theme, language
        self.runtime = context.services.resolve(CompetitionRuntime)
        self.popup = self.panel = None
        self.indicator = StatusChip("TEKNOFEST", self)
        self.indicator.setToolTip(t("extension.siha_teknofest_server.name", language))
        self.indicator.set_theme(theme)
        self.indicator.clicked.connect(self.open)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.indicator)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def open(self):
        if self.panel is None:
            self.panel = ServerPanel(self.context, self, popup_mode=True)
            self.popup = self.panel.popup
            self.panel.set_theme(self.theme)
            self.panel.set_language(self.language)
        self.panel.open(self.indicator)

    def refresh(self):
        self.indicator.set_state(
            "success"
            if any(item.connected for item in self.runtime.sessions.copy().values())
            else "danger"
        )
        if (
            self.panel is not None
            and not self.popup.isVisible()
            and self.panel.pending is None
        ):
            self.panel.timer.stop()

    def set_theme(self, theme):
        self.theme = theme
        self.indicator.set_theme(theme)
        if self.panel is not None:
            self.panel.set_theme(theme)

    def set_language(self, language):
        self.language = language
        self.indicator.setToolTip(t("extension.siha_teknofest_server.name", language))
        if self.panel is not None:
            self.panel.set_language(language)

    def prepare_shutdown(self):
        self.timer.stop()
        if self.panel is not None:
            self.panel.prepare_shutdown()
