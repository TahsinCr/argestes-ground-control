from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.models.connections import ConnectionStatus
from src.ui.themes.style import ThemePalette


VEHICLE_CHANNELS = frozenset({"telemetry", "autopilot", "gps", "orin"})


class ConnectionPopup(QDialog):
    reconnect_requested = pyqtSignal(str, dict)
    disconnect_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()

    TITLES = {
        "server": "TEKNOFEST SUNUCU BAĞLANTISI",
        "telemetry": "TELEMETRİ BAĞLANTISI",
        "video": "VİDEO BAĞLANTISI",
        "autopilot": "OTOPİLOT BAĞLANTISI",
        "gps": "GPS BAĞLANTISI",
        "orin": "ORIN UYGULAMA BAĞLANTISI",
    }

    def __init__(self, theme: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("ConnectionPopup")
        self.setMinimumWidth(370)
        self._theme = theme
        self._channel = ""
        self._busy = False
        self._can_disconnect = False
        self._build_ui()
        self.set_theme(theme)

    @property
    def channel(self) -> str:
        return self._channel

    def show_status(self, status: ConnectionStatus) -> None:
        self._channel = status.channel
        self.title_label.setText(self.TITLES.get(status.channel, status.channel.upper()))
        self._apply_status(status)
        self.endpoint_input.setText(status.endpoint)
        self.timeout_input.setValue(max(0.1, float(status.timeout_s or 5.0)))

        is_vehicle = status.channel in VEHICLE_CHANNELS
        is_video = status.channel == "video"
        is_server = status.channel == "server"
        self.endpoint_input.setReadOnly(False)
        self.endpoint_label.setText(
            "TEKNOFEST adresi" if is_server else ("Kaynak URI" if is_video else "MAVLink adresi")
        )
        self.timeout_label.setText("Akış zaman aşımı" if is_video else "Zaman aşımı")
        self.timeout_input.setSuffix(" s")

        self.baud_label.setVisible(is_vehicle)
        self.baud_input.setVisible(is_vehicle)
        self.auto_connect.setVisible(is_vehicle or is_server)
        self.auto_connect.setText(
            "Uygulama açıldığında AREF telemetri hattına otomatik bağlan"
            if is_vehicle
            else "Uygulama açıldığında otomatik bağlan"
        )
        self.baud_input.setValue(int(status.options.get("baud", 115200)))
        self.auto_connect.setChecked(bool(status.options.get("auto_connect", False)))

        for widget in (
            self.username_label,
            self.username_input,
            self.password_label,
            self.password_input,
            self.team_number_label,
            self.team_number_input,
            self.telemetry_period_label,
            self.telemetry_period_input,
        ):
            widget.setVisible(is_server)
        self.username_input.setText(str(status.options.get("username", "")))
        self.password_input.clear()
        self.password_input.setPlaceholderText("Değiştirmemek için boş bırakın")
        self.team_number_input.setValue(int(status.options.get("team_number", 1)))
        self.telemetry_period_input.setValue(
            int(status.options.get("telemetry_period_ms", 1000))
        )

        self.source_type_label.setVisible(is_video)
        self.source_type_input.setVisible(is_video)
        self.codec_label.setVisible(is_video)
        self.codec_input.setVisible(is_video)
        self.video_latency_label.setVisible(is_video)
        self.video_latency_input.setVisible(is_video)
        self.source_type_input.setCurrentText(str(status.options.get("source_type", "udp")))
        self.codec_input.setCurrentText(str(status.options.get("codec", "h265")))
        if is_video:
            self.video_latency_input.setValue(int(status.options.get("latency_ms", 100)))

        self._can_disconnect = status.can_disconnect
        self._sync_reconnect_label(status)
        self.set_busy(False)

    def refresh_status(self, status: ConnectionStatus) -> None:
        """Canlı sağlık bilgisini, kullanıcının form değerlerini ezmeden yeniler."""
        if status.channel != self._channel:
            return
        self._apply_status(status)
        self._can_disconnect = status.can_disconnect
        if not self._busy:
            self.disconnect_button.setEnabled(self._can_disconnect)
        self._sync_reconnect_label(status)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            QDialog#ConnectionPopup {{
                background: {theme.surface};
                border: 1px solid {theme.border_strong};
                border-radius: 7px;
            }}
            QLabel {{ background: transparent; color: {theme.text}; }}
            QLabel[role="popup-title"] {{ font-size: 11px; font-weight: 750; }}
            QLabel[role="popup-detail"] {{ color: {theme.muted_text}; font-size: 10px; }}
            QFrame#PopupStatus {{
                background: {theme.surface_alt};
                border: 1px solid {theme.border};
                border-radius: 5px;
            }}
            """
        )

    def set_busy(self, busy: bool, message: str = "Bağlantı deneniyor…") -> None:
        self._busy = bool(busy)
        self.reconnect_button.setDisabled(busy)
        self.disconnect_button.setDisabled(busy or not self._can_disconnect)
        if busy:
            self.result_label.setText(message)
            self.result_label.setStyleSheet(f"color: {self._theme.warning};")

    def finish_operation(
        self,
        success: bool,
        message: str,
        status: ConnectionStatus,
    ) -> None:
        self.show_status(status)
        self.result_label.setText(message)
        color = self._theme.success if success else self._theme.danger
        self.result_label.setStyleSheet(f"color: {color};")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(9)

        self.title_label = QLabel(self)
        self.title_label.setProperty("role", "popup-title")
        root.addWidget(self.title_label)

        status_box = QFrame(self)
        status_box.setObjectName("PopupStatus")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(9, 7, 9, 7)
        status_layout.setSpacing(2)
        self.summary_label = QLabel(status_box)
        self.summary_label.setStyleSheet("font-weight: 700;")
        self.detail_label = QLabel(status_box)
        self.detail_label.setProperty("role", "popup-detail")
        self.detail_label.setWordWrap(True)
        status_layout.addWidget(self.summary_label)
        status_layout.addWidget(self.detail_label)
        root.addWidget(status_box)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self.endpoint_label = QLabel("Adres", self)
        self.endpoint_input = QLineEdit(self)
        form.addRow(self.endpoint_label, self.endpoint_input)

        self.timeout_label = QLabel("Zaman aşımı", self)
        self.timeout_input = QDoubleSpinBox(self)
        self.timeout_input.setRange(1.0, 120.0)
        self.timeout_input.setDecimals(0)
        self.timeout_input.setSingleStep(1.0)
        self.timeout_input.setSuffix(" s")
        form.addRow(self.timeout_label, self.timeout_input)

        self.username_label = QLabel("Kullanıcı adı", self)
        self.username_input = QLineEdit(self)
        form.addRow(self.username_label, self.username_input)

        self.password_label = QLabel("Şifre", self)
        self.password_input = QLineEdit(self)
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(self.password_label, self.password_input)

        self.team_number_label = QLabel("Takım numarası", self)
        self.team_number_input = QSpinBox(self)
        self.team_number_input.setRange(1, 9999)
        form.addRow(self.team_number_label, self.team_number_input)

        self.telemetry_period_label = QLabel("Telemetri periyodu", self)
        self.telemetry_period_input = QSpinBox(self)
        self.telemetry_period_input.setRange(500, 60_000)
        self.telemetry_period_input.setSingleStep(100)
        self.telemetry_period_input.setSuffix(" ms")
        form.addRow(self.telemetry_period_label, self.telemetry_period_input)

        self.baud_label = QLabel("Baud", self)
        self.baud_input = QSpinBox(self)
        self.baud_input.setRange(1200, 4_000_000)
        self.baud_input.setSingleStep(9600)
        form.addRow(self.baud_label, self.baud_input)

        self.source_type_label = QLabel("Kaynak türü", self)
        self.source_type_input = QComboBox(self)
        self.source_type_input.addItems(
            ["http", "udp", "rtsp", "usb", "file", "pipeline"]
        )
        form.addRow(self.source_type_label, self.source_type_input)

        self.codec_label = QLabel("Codec", self)
        self.codec_input = QComboBox(self)
        self.codec_input.addItems(["mjpeg", "h265", "h264"])
        form.addRow(self.codec_label, self.codec_input)

        self.video_latency_label = QLabel("Pipeline gecikmesi", self)
        self.video_latency_input = QSpinBox(self)
        self.video_latency_input.setRange(0, 10_000)
        self.video_latency_input.setSuffix(" ms")
        form.addRow(self.video_latency_label, self.video_latency_input)
        root.addLayout(form)

        self.auto_connect = QCheckBox("Uygulama açıldığında otomatik bağlan", self)
        root.addWidget(self.auto_connect)
        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        self.result_label.setProperty("role", "popup-detail")
        root.addWidget(self.result_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.settings_button = QPushButton("Tüm Ayarlar", self)
        self.disconnect_button = QPushButton("Bağlantıyı Kes", self)
        self.disconnect_button.setProperty("role", "danger")
        self.reconnect_button = QPushButton("Tekrar Dene", self)
        self.reconnect_button.setProperty("role", "primary")
        buttons.addWidget(self.settings_button)
        buttons.addStretch(1)
        buttons.addWidget(self.disconnect_button)
        buttons.addWidget(self.reconnect_button)
        root.addLayout(buttons)

        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.disconnect_button.clicked.connect(
            lambda: self.disconnect_requested.emit(self._channel)
        )
        self.reconnect_button.clicked.connect(self._request_reconnect)

    def _request_reconnect(self) -> None:
        options: dict[str, object] = {
            "endpoint": self.endpoint_input.text().strip(),
            "timeout_s": self.timeout_input.value(),
        }
        if self._channel in VEHICLE_CHANNELS:
            options.update(
                baud=self.baud_input.value(),
                auto_connect=self.auto_connect.isChecked(),
            )
        elif self._channel == "video":
            options.update(
                source_type=self.source_type_input.currentText(),
                codec=self.codec_input.currentText(),
                latency_ms=self.video_latency_input.value(),
                frame_timeout_s=self.timeout_input.value(),
            )
        elif self._channel == "server":
            options.update(
                username=self.username_input.text().strip(),
                password=self.password_input.text(),
                team_number=self.team_number_input.value(),
                telemetry_period_ms=self.telemetry_period_input.value(),
                auto_connect=self.auto_connect.isChecked(),
            )
        self.reconnect_requested.emit(self._channel, options)

    def _apply_status(self, status: ConnectionStatus) -> None:
        marker = "●"
        color = self._theme.success if status.connected else self._theme.danger
        self.summary_label.setText(f"{marker}  {status.summary}")
        self.summary_label.setStyleSheet(f"color: {color}; font-weight: 700;")
        self.detail_label.setText(status.detail)

    def _sync_reconnect_label(self, status: ConnectionStatus) -> None:
        self.reconnect_button.setText("Yeniden Bağlan" if status.connected else "Bağlan")
