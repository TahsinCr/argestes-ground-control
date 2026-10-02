from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from extensions.joystick.models import (
    JoystickButtonConfig,
    JoystickChannelConfig,
    JoystickSettings,
)
from extensions.joystick.contracts import JoystickService
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette
from src.ui.widgets.title_bar import AppTitleBar


class JoystickDialog(QDialog):
    """Mission-Planner-style RC joystick assignment and calibration editor."""

    def __init__(
        self,
        settings_service,
        joystick_service: JoystickService,
        language: str,
        theme: ThemePalette,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings_service = settings_service
        self._joystick = joystick_service
        self._language = language
        self._channel_inputs: list[QComboBox] = []
        self._pwm_labels: list[QLabel] = []
        self._minimums: list[QSpinBox] = []
        self._trims: list[QSpinBox] = []
        self._maximums: list[QSpinBox] = []
        self._deadzones: list[QDoubleSpinBox] = []
        self._expos: list[QSpinBox] = []
        self._reverses: list[QCheckBox] = []
        self._button_actions: list[QComboBox] = []
        self._button_controls: list[QComboBox] = []
        self._button_live: list[QLabel] = []
        self._button_settings: list[QPushButton] = []
        self._button_modes = [""] * 10
        self._button_servos = [1] * 10
        self._button_pwms = [1500] * 10
        self._detecting_channel: int | None = None
        self._detect_axis_baseline: tuple[float, ...] = ()
        self._detect_button_baseline: tuple[bool, ...] = ()
        self._configured_controls: list[str] = []
        self._configured_device = ""
        self._enabled = False
        self._configured_buttons: list[JoystickButtonConfig] = []

        self.setObjectName("FramelessDialog")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.resize(1080, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        title = AppTitleBar(self, theme, self)
        title.set_title(t("joystick.title", language))
        root.addWidget(title)

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(9)
        layout.addLayout(self._build_device_bar())
        tabs = QTabWidget(body)
        tabs.addTab(
            self._build_channel_table(), t("joystick.channels_tab", self._language)
        )
        tabs.addTab(
            self._build_button_table(), t("joystick.buttons_tab", self._language)
        )
        layout.addWidget(tabs, 1)
        layout.addLayout(self._build_footer())
        root.addWidget(body, 1)

        self._load(
            JoystickSettings.from_config(
                self._settings_service.extension_config("joystick")
            )
        )
        self._refresh_devices()
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(50)
        self._live_timer.timeout.connect(self._update_live_values)
        self._live_timer.start()

    def _build_device_bar(self) -> QGridLayout:
        layout = QGridLayout()
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        layout.addWidget(QLabel(t("joystick.device", self._language), self), 0, 0)
        self.device_combo = QComboBox(self)
        self.device_combo.currentIndexChanged.connect(self._populate_inputs)
        layout.addWidget(self.device_combo, 0, 1, 1, 4)
        refresh = QPushButton(t("joystick.refresh", self._language), self)
        refresh.clicked.connect(self._refresh_devices)
        layout.addWidget(refresh, 0, 5)
        self.activation_button = QPushButton(self)
        self.activation_button.clicked.connect(self._toggle_activation)
        layout.addWidget(self.activation_button, 0, 6)

        layout.addWidget(QLabel(t("joystick.rate", self._language), self), 1, 0)
        self.rate_input = QSpinBox(self)
        self.rate_input.setRange(5, 50)
        self.rate_input.setSuffix(" Hz")
        layout.addWidget(self.rate_input, 1, 1)
        self.device_status = QLabel(self)
        self.device_status.setProperty("role", "muted")
        layout.addWidget(self.device_status, 1, 2, 1, 5)
        return layout

    def _build_channel_table(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        grid = QGridLayout(content)
        grid.setContentsMargins(3, 5, 3, 5)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)
        headers = (
            "joystick.channel",
            "joystick.control",
            "joystick.detect",
            "joystick.output",
            "joystick.minimum",
            "joystick.trim",
            "joystick.maximum",
            "joystick.deadzone",
            "joystick.expo",
            "joystick.reverse",
        )
        for column, key in enumerate(headers):
            label = QLabel(t(key, self._language), content)
            label.setProperty("role", "section-heading")
            grid.addWidget(label, 0, column)

        for index in range(16):
            grid.addWidget(QLabel(f"RC {index + 1}", content), index + 1, 0)
            control = QComboBox(content)
            control.setMinimumWidth(145)
            self._channel_inputs.append(control)
            grid.addWidget(control, index + 1, 1)
            detect = QPushButton(t("joystick.auto_detect", self._language), content)
            detect.setProperty("compact", "true")
            detect.clicked.connect(
                lambda _checked=False, channel=index: self._start_detect(channel)
            )
            grid.addWidget(detect, index + 1, 2)
            pwm = QLabel("—", content)
            pwm.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pwm.setMinimumWidth(54)
            pwm.setProperty("role", "value")
            self._pwm_labels.append(pwm)
            grid.addWidget(pwm, index + 1, 3)
            minimum = self._pwm_spin(content)
            trim = self._pwm_spin(content)
            maximum = self._pwm_spin(content)
            self._minimums.append(minimum)
            self._trims.append(trim)
            self._maximums.append(maximum)
            grid.addWidget(minimum, index + 1, 4)
            grid.addWidget(trim, index + 1, 5)
            grid.addWidget(maximum, index + 1, 6)
            deadzone = QDoubleSpinBox(content)
            deadzone.setRange(0.0, 0.5)
            deadzone.setDecimals(2)
            deadzone.setSingleStep(0.01)
            self._deadzones.append(deadzone)
            grid.addWidget(deadzone, index + 1, 7)
            expo = QSpinBox(content)
            expo.setRange(0, 100)
            expo.setSuffix(" %")
            self._expos.append(expo)
            grid.addWidget(expo, index + 1, 8)
            reverse = QCheckBox(content)
            self._reverses.append(reverse)
            grid.addWidget(
                reverse, index + 1, 9, alignment=Qt.AlignmentFlag.AlignCenter
            )
        grid.setColumnStretch(1, 1)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _pwm_spin(parent: QWidget) -> QSpinBox:
        spin = QSpinBox(parent)
        spin.setRange(800, 2200)
        spin.setSingleStep(10)
        spin.setSuffix(" µs")
        return spin

    def _build_button_table(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        grid = QGridLayout(content)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for column, key in enumerate(
            (
                "joystick.button_header",
                "joystick.control",
                "joystick.detect",
                "joystick.output",
                "joystick.action",
                "joystick.action_settings",
            )
        ):
            label = QLabel(t(key, self._language), content)
            label.setProperty("role", "section-heading")
            grid.addWidget(label, 0, column)
        for index in range(10):
            grid.addWidget(
                QLabel(
                    t("joystick.button_number", self._language, number=index + 1),
                    content,
                ),
                index + 1,
                0,
            )
            control = QComboBox(content)
            detect = QPushButton(t("joystick.auto_detect", self._language), content)
            detect.setProperty("compact", "true")
            detect.clicked.connect(
                lambda _checked=False, row=index: self._start_button_detect(row)
            )
            live = QLabel("—", content)
            live.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._button_controls.append(control)
            self._button_live.append(live)
            action = QComboBox(content)
            for key, code in (
                ("joystick.action_none", ""),
                ("joystick.action_arm", "ARM"),
                ("joystick.action_disarm", "DISARM"),
                ("joystick.action_mode", "SET_MODE"),
                ("joystick.action_servo", "SET_SERVO"),
            ):
                action.addItem(t(key, self._language), code)
            settings = QPushButton(t("joystick.settings", self._language), content)
            settings.setProperty("compact", "true")
            settings.setEnabled(False)
            settings.clicked.connect(
                lambda _checked=False, row=index: self._edit_button_settings(row)
            )
            action.currentIndexChanged.connect(
                lambda _index, action=action, settings=settings: settings.setEnabled(
                    action.currentData() in {"SET_MODE", "SET_SERVO"}
                )
            )
            self._button_actions.append(action)
            self._button_settings.append(settings)
            grid.addWidget(control, index + 1, 1)
            grid.addWidget(detect, index + 1, 2)
            grid.addWidget(live, index + 1, 3)
            grid.addWidget(action, index + 1, 4)
            grid.addWidget(settings, index + 1, 5)
        grid.setColumnStretch(5, 1)
        scroll.setWidget(content)
        return scroll

    def _build_footer(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        self.hint = QLabel(t("joystick.safety_hint", self._language), self)
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint, 1)
        cancel = QPushButton(t("dialog.settings.cancel", self._language), self)
        self.cancel_button = cancel
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        save = QPushButton(t("dialog.settings.save", self._language), self)
        save.setProperty("role", "primary")
        save.clicked.connect(self._save_and_close)
        layout.addWidget(save)
        return layout

    def _load(self, config: JoystickSettings) -> None:
        self._enabled = config.enabled
        self._configured_device = config.device_path
        self._configured_controls = [channel.control for channel in config.channels]
        self._configured_buttons = list(config.buttons)
        self.rate_input.setValue(config.send_rate_hz)
        for index, channel in enumerate(config.channels):
            self._minimums[index].setValue(channel.pwm_min)
            self._trims[index].setValue(channel.pwm_trim)
            self._maximums[index].setValue(channel.pwm_max)
            self._deadzones[index].setValue(channel.deadzone)
            self._expos[index].setValue(channel.expo)
            self._reverses[index].setChecked(channel.reversed)
        for index, action_input in enumerate(self._button_actions):
            assignment = next(
                (
                    item
                    for item in config.buttons
                    if (item.slot or config.buttons.index(item) + 1) == index + 1
                ),
                None,
            )
            action = assignment.action if assignment else ""
            action_index = action_input.findData(action)
            action_input.setCurrentIndex(max(0, action_index))
            if assignment:
                self._button_modes[index] = assignment.mode
                self._button_servos[index] = assignment.servo or 1
                self._button_pwms[index] = assignment.pwm
        self._update_activation_text()

    def _refresh_devices(self) -> None:
        selected = str(self.device_combo.currentData() or self._configured_device)
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in self._joystick.devices():
            self.device_combo.addItem(
                f"{device.name} · {device.path} · {device.axis_count}A/{device.button_count}B",
                device.path,
            )
            self.device_combo.setItemData(
                self.device_combo.count() - 1,
                (device.axis_count, device.button_count),
                Qt.ItemDataRole.UserRole + 1,
            )
        if selected and self.device_combo.findData(selected) < 0:
            self.device_combo.addItem(
                f"{selected} ({t('joystick.unavailable', self._language)})",
                selected,
            )
        selected_index = self.device_combo.findData(selected)
        self.device_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.device_combo.blockSignals(False)
        self._populate_inputs()

    def _populate_inputs(self) -> None:
        metadata = self.device_combo.currentData(Qt.ItemDataRole.UserRole + 1) or (0, 0)
        axis_count, button_count = metadata
        for row, combo in enumerate(self._channel_inputs):
            selected = str(combo.currentData() or self._configured_controls[row])
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(t("joystick.none", self._language), "")
            for index in range(int(axis_count)):
                combo.addItem(
                    t("joystick.axis", self._language, number=index + 1),
                    f"axis:{index}",
                )
            for index in range(int(button_count)):
                combo.addItem(
                    t("joystick.button", self._language, number=index + 1),
                    f"button:{index}",
                )
                combo.addItem(
                    t("joystick.toggle", self._language, number=index + 1),
                    f"toggle:{index}",
                )
            index = combo.findData(selected)
            if selected and index < 0:
                combo.addItem(
                    f"{selected} ({t('joystick.unavailable', self._language)})",
                    selected,
                )
                index = combo.count() - 1
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
        for row, combo in enumerate(self._button_controls):
            selected = combo.currentData()
            assignment = next(
                (
                    item
                    for item in self._configured_buttons
                    if (item.slot or self._configured_buttons.index(item) + 1)
                    == row + 1
                ),
                None,
            )
            if selected is None and assignment is not None and assignment.button >= 0:
                selected = assignment.button
            combo.clear()
            combo.addItem(t("joystick.none", self._language), None)
            for index in range(int(button_count)):
                combo.addItem(
                    t("joystick.button_number", self._language, number=index + 1), index
                )
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(max(0, selected_index))

    def _start_detect(self, channel: int) -> None:
        state = self._joystick.state
        if not state.connected:
            self.hint.setText(t("joystick.activate_to_detect", self._language))
            return
        self._detecting_channel = channel
        self._detect_axis_baseline = state.axes
        self._detect_button_baseline = state.buttons
        self.hint.setText(
            t("joystick.move_control", self._language, channel=channel + 1)
        )

    def _update_live_values(self) -> None:
        if self.property("embeddedSettings") and not self.isVisible():
            return
        state = self._joystick.state
        if state.connected and state.transmitting:
            self.device_status.setText(t("joystick.transmitting", self._language))
            self.device_status.setStyleSheet("color: #45c97a;")
        elif state.connected:
            self.device_status.setText(t("joystick.waiting_vehicle", self._language))
            self.device_status.setStyleSheet("color: #e8b34f;")
        elif state.error:
            self.device_status.setText(state.error)
            self.device_status.setStyleSheet("color: #ef646b;")
        else:
            self.device_status.setText(t("joystick.disconnected", self._language))
            self.device_status.setStyleSheet("")
        for index, pwm in enumerate(state.channel_pwm):
            self._pwm_labels[index].setText("—" if pwm is None else str(pwm))
        for row, combo in enumerate(self._button_controls):
            button = combo.currentData()
            pressed = (
                button is not None
                and int(button) < len(state.buttons)
                and state.buttons[int(button)]
            )
            self._button_live[row].setText("1" if pressed else "0")
            self._button_live[row].setStyleSheet("color: #45c97a;" if pressed else "")
        if self._detecting_channel is not None:
            self._detect_input(state.axes, state.buttons)

    def _detect_input(self, axes: tuple[float, ...], buttons: tuple[bool, ...]) -> None:
        if self._detecting_channel is not None and self._detecting_channel >= 0:
            for index, value in enumerate(axes):
                baseline = (
                    self._detect_axis_baseline[index]
                    if index < len(self._detect_axis_baseline)
                    else 0.0
                )
                if abs(value - baseline) >= 0.35:
                    self._finish_detect(f"axis:{index}")
                    return
        for index, pressed in enumerate(buttons):
            baseline = (
                self._detect_button_baseline[index]
                if index < len(self._detect_button_baseline)
                else False
            )
            if pressed and not baseline:
                if self._detecting_channel is not None and self._detecting_channel < 0:
                    row = -self._detecting_channel - 1
                    combo = self._button_controls[row]
                    option = combo.findData(index)
                    if option >= 0:
                        combo.setCurrentIndex(option)
                    self._detecting_channel = None
                    self.hint.setText(t("joystick.safety_hint", self._language))
                    return
                self._finish_detect(f"button:{index}")
                return

    def _finish_detect(self, control: str) -> None:
        if self._detecting_channel is None:
            return
        combo = self._channel_inputs[self._detecting_channel]
        index = combo.findData(control)
        if index >= 0:
            combo.setCurrentIndex(index)
        self._detecting_channel = None
        self.hint.setText(t("joystick.safety_hint", self._language))

    def _start_button_detect(self, row: int) -> None:
        state = self._joystick.state
        if not state.connected:
            self.hint.setText(t("joystick.activate_to_detect", self._language))
            return
        self._detecting_channel = -(row + 1)
        self._detect_button_baseline = state.buttons
        self.hint.setText(t("joystick.press_button", self._language, row=row + 1))

    def _edit_button_settings(self, row: int) -> None:
        action = str(self._button_actions[row].currentData() or "")
        mode: QComboBox | None = None
        servo: QSpinBox | None = None
        pwm: QSpinBox | None = None
        dialog = QDialog(self)
        dialog.setWindowTitle(t("joystick.settings", self._language))
        root = QVBoxLayout(dialog)
        form = QFormLayout()
        root.addLayout(form)
        if action == "SET_MODE":
            mode = QComboBox(dialog)
            mode.setEditable(True)
            mode.addItems(
                (
                    "MANUAL",
                    "STABILIZE",
                    "ALT_HOLD",
                    "LOITER",
                    "GUIDED",
                    "AUTO",
                    "RTL",
                    "LAND",
                )
            )
            mode.setCurrentText(self._button_modes[row] or "GUIDED")
            form.addRow(t("joystick.mode", self._language), mode)
        elif action == "SET_SERVO":
            servo = QSpinBox(dialog)
            servo.setRange(1, 16)
            servo.setValue(self._button_servos[row])
            pwm = QSpinBox(dialog)
            pwm.setRange(800, 2200)
            pwm.setSuffix(" µs")
            pwm.setValue(self._button_pwms[row])
            form.addRow(t("joystick.servo", self._language), servo)
            form.addRow(t("joystick.servo_pwm", self._language), pwm)
        else:
            return
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if action == "SET_MODE":
            assert mode is not None
            self._button_modes[row] = mode.currentText().strip().upper()
        else:
            assert servo is not None and pwm is not None
            self._button_servos[row] = servo.value()
            self._button_pwms[row] = pwm.value()

    def _configuration(self, enabled: bool | None = None) -> JoystickSettings:
        return JoystickSettings(
            enabled=self._enabled if enabled is None else enabled,
            device_path=str(self.device_combo.currentData() or ""),
            send_rate_hz=self.rate_input.value(),
            channels=[
                JoystickChannelConfig(
                    channel=index + 1,
                    control=str(self._channel_inputs[index].currentData() or ""),
                    pwm_min=self._minimums[index].value(),
                    pwm_trim=self._trims[index].value(),
                    pwm_max=self._maximums[index].value(),
                    deadzone=self._deadzones[index].value(),
                    expo=self._expos[index].value(),
                    reversed=self._reverses[index].isChecked(),
                )
                for index in range(16)
            ],
            buttons=[
                JoystickButtonConfig(
                    button=int(
                        self._button_controls[index].currentData()
                        if self._button_controls[index].currentData() is not None
                        else -1
                    ),
                    action=str(self._button_actions[index].currentData() or ""),
                    mode=self._button_modes[index],
                    servo=self._button_servos[index],
                    pwm=self._button_pwms[index],
                    slot=index + 1,
                )
                for index in range(10)
                if self._button_controls[index].currentData() is not None
                or self._button_actions[index].currentData()
            ],
        )

    def _persist(self, config: JoystickSettings) -> None:
        payload = {
            "enabled": config.enabled,
            "device_path": config.device_path,
            "send_rate_hz": config.send_rate_hz,
            "channels": [channel.to_dict() for channel in config.channels],
            "buttons": [button.to_dict() for button in config.buttons],
        }
        self._settings_service.update_extension("joystick", config=payload)
        self._joystick.reconnect()

    def _toggle_activation(self) -> None:
        if not self._enabled and not self.device_combo.currentData():
            QMessageBox.warning(
                self,
                t("joystick.title", self._language),
                t("joystick.select_device", self._language),
            )
            return
        try:
            self._enabled = not self._enabled
            self._persist(self._configuration(enabled=self._enabled))
        except (TypeError, ValueError) as exc:
            self._enabled = not self._enabled
            QMessageBox.warning(self, t("joystick.title", self._language), str(exc))
            return
        if self._enabled:
            self._joystick.reconnect()
        else:
            self._joystick.disconnect()
        self._update_activation_text()

    def _update_activation_text(self) -> None:
        self.activation_button.setText(
            t(
                "joystick.deactivate" if self._enabled else "joystick.activate",
                self._language,
            )
        )
        self.activation_button.setProperty("danger", self._enabled)
        self.activation_button.style().unpolish(self.activation_button)
        self.activation_button.style().polish(self.activation_button)

    def _save_and_close(self) -> None:
        try:
            self._persist(self._configuration())
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, t("joystick.title", self._language), str(exc))
            return
        self.accept()

    def done(self, result):
        if self.property("embeddedSettings"):
            if result == QDialog.DialogCode.Accepted:
                self.accepted.emit()
            return
        self._live_timer.stop()
        super().done(result)


__all__ = ["JoystickDialog"]
