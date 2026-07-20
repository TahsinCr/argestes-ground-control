import os
import signal
import sys
from pathlib import Path


def _sanitize_graphics_environment() -> None:
    """Qt/Mesa'nın geçersiz GPU seçim değerleriyle başlamasını engeller."""

    dri_prime = os.environ.get("DRI_PRIME")
    if dri_prime is not None and dri_prime.strip() in {"", "0"}:
        os.environ.pop("DRI_PRIME", None)


_sanitize_graphics_environment()

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.ui.adapters.runtime import create_qt_runtime_services
from src.ui.themes.manager import apply_theme
from src.ui.views.main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Kirlangic Team YKİ")
    app.setOrganizationName("Kirlangic Team")

    base_dir = Path(__file__).resolve().parent.parent
    runtime = create_qt_runtime_services(base_dir)
    theme = apply_theme(app, runtime.settings_service.current.theme)

    window = MainWindow(
        log_service=runtime.log_service,
        settings_service=runtime.settings_service,
        telemetry_service=runtime.telemetry_service,
        mission_service=runtime.mission_service,
        command_service=runtime.command_service,
        parameter_service=runtime.parameter_service,
        video_service=runtime.video_service,
        connection_service=runtime.connection_service,
        tile_cache_service=runtime.tile_cache_service,
        archive_service=runtime.archive_service,
        base_dir=base_dir,
        theme=theme,
    )
    window.show()

    app.aboutToQuit.connect(runtime.stop)
    previous_sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda _signum, _frame: app.quit())
    signal_timer = QTimer(app)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(250)
    runtime.start()

    try:
        return app.exec()
    finally:
        signal_timer.stop()
        signal.signal(signal.SIGINT, previous_sigint_handler)
        runtime.stop()
