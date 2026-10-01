"""Non-blocking server configuration; no HTTP or SQL work on the UI thread."""
from uuid import uuid4
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QHBoxLayout,
                            QListWidget, QLineEdit, QSpinBox, QCheckBox, QPushButton,
                            QLabel, QMessageBox)
from src.services.settings import SettingsService
from src.ui.i18n import t
from .runtime import CompetitionRuntime


class ServerPanel(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.settings = context.services.resolve(SettingsService)
        self.runtime = context.services.resolve(CompetitionRuntime)
        self.language = self.settings.current.ui.language
        self.profiles = [dict(item) for item in self.settings.extension_config("teknofest").get("servers", [])]
        self.pending = None
        layout = QVBoxLayout(self)
        self.list = QListWidget(self)
        layout.addWidget(self.list)
        form = QFormLayout()
        self.fields = {}
        for key in ("name", "base_url", "username", "password"):
            field = QLineEdit(self)
            if key == "password":
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[key] = field
            form.addRow(t("teknofest." + key, self.language), field)
        self.team = QSpinBox(self)
        self.team.setRange(1, 9999)
        form.addRow(t("teknofest.team", self.language), self.team)
        self.auto = QCheckBox(t("teknofest.auto", self.language), self)
        form.addRow(self.auto)
        layout.addLayout(form)
        self.controls = QWidget(self)
        actions = QHBoxLayout(self.controls)
        for key, callback in (("add", self.add), ("remove", self.remove),
                              ("save", self.save), ("connect", self.connect), ("disconnect", self.disconnect)):
            button = QPushButton(t("teknofest." + key, self.language), self.controls)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addWidget(self.controls)
        self.status = QLabel(self)
        layout.addWidget(self.status)
        self.list.currentRowChanged.connect(self.select)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.rebuild()

    def rebuild(self):
        self.list.clear()
        self.list.addItems([item.get("name", "TEKNOFEST") for item in self.profiles])
        if self.profiles:
            self.list.setCurrentRow(0)

    def select(self, index):
        if not 0 <= index < len(self.profiles):
            return
        profile = self.profiles[index]
        for key, field in self.fields.items():
            field.setText(str(profile.get(key, "")))
        self.team.setValue(int(profile.get("team_number", 1)))
        self.auto.setChecked(bool(profile.get("auto_connect", False)))

    def add(self):
        self.profiles.append({"identifier": uuid4().hex, "name": "TEKNOFEST",
                              "base_url": "http://127.0.0.1:5000", "timeout_s": 3,
                              "auto_connect": False})
        self.rebuild()
        self.list.setCurrentRow(len(self.profiles) - 1)

    def remove(self):
        index = self.list.currentRow()
        if index >= 0:
            self.profiles.pop(index)
            self.rebuild()
            try:
                self.pending = self.runtime.configure([dict(item) for item in self.profiles])
            except ValueError as error:
                QMessageBox.warning(self, "TEKNOFEST", str(error))
                return
            self.controls.setEnabled(False)

    def save(self):
        index = self.list.currentRow()
        if index < 0:
            return
        profile = dict(self.profiles[index])
        profile.update({key: field.text().strip() for key, field in self.fields.items()})
        if not profile["base_url"].startswith(("http://", "https://")):
            QMessageBox.warning(self, "TEKNOFEST", t("teknofest.invalid_url", self.language))
            return
        profile.update(team_number=self.team.value(), auto_connect=self.auto.isChecked())
        self.profiles[index] = profile
        self.list.item(index).setText(profile["name"])
        try:
            self.pending = self.runtime.configure([dict(item) for item in self.profiles])
        except ValueError as error:
            QMessageBox.warning(self, "TEKNOFEST", str(error))
            return
        self.controls.setEnabled(False)

    def connect(self):
        index = self.list.currentRow()
        if index >= 0 and self.profiles[index]["identifier"] in self.runtime.sessions:
            self.runtime.connect(self.profiles[index]["identifier"])

    def disconnect(self):
        index = self.list.currentRow()
        if index >= 0 and self.profiles[index]["identifier"] in self.runtime.sessions:
            self.pending = self.runtime.disconnect(self.profiles[index]["identifier"])

    def refresh(self):
        if self.pending is not None and self.pending.done():
            error = self.pending.exception()
            self.pending = None
            self.controls.setEnabled(True)
            if error:
                self.status.setText(str(error))
                return
        index = self.list.currentRow()
        if index >= 0:
            session = self.runtime.sessions.get(self.profiles[index]["identifier"])
            if session:
                self.status.setText(session.error or t("teknofest.connected" if session.connected else "teknofest.disconnected", self.language))

    def prepare_shutdown(self):
        self.timer.stop()
