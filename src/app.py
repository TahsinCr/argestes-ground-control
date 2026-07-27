import os
import signal
import sys
import threading
from pathlib import Path


def _sanitize_graphics_environment() -> None:
    """Prevent Qt/Mesa from starting with invalid GPU selection values."""

    dri_prime = os.environ.get("DRI_PRIME")
    if dri_prime is not None and dri_prime.strip() in {"", "0"}:
        os.environ.pop("DRI_PRIME", None)


_sanitize_graphics_environment()

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.ui.assets import application_icon
from src.ui.adapters.runtime import create_qt_runtime_services
from src.ui.i18n import t
from src.ui.themes.manager import apply_theme
from src.ui.views.main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Kırlangıç Team YKİ")
    app.setOrganizationName("Kırlangıç Team")

    base_dir = Path(__file__).resolve().parent.parent
    app.setWindowIcon(application_icon(base_dir))
    runtime = create_qt_runtime_services(base_dir)
    app.setApplicationName(
        t("app.title", runtime.settings_service.current.language)
    )
    theme = apply_theme(app, runtime.settings_service.current.theme)

    window = MainWindow(
        log_service=runtime.log_service,
        settings_service=runtime.settings_service,
        telemetry_service=runtime.telemetry_service,
        planner_service=runtime.planner_service,
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

    shutdown_signal = threading.Event()
    shutdown_started = False
    signal_timer = QTimer(app)

    def stop_runtime_once() -> None:
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        signal_timer.stop()
        window.prepare_shutdown()
        runtime.stop()

    def request_shutdown(_signum=None, _frame=None) -> None:
        shutdown_signal.set()

    def process_shutdown_request() -> None:
        if shutdown_signal.is_set() and not shutdown_started:
            window.close()

    window.shutdown_requested.connect(stop_runtime_once)
    app.aboutToQuit.connect(stop_runtime_once)
    previous_sigint_handler = signal.getsignal(signal.SIGINT)
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    signal_timer.timeout.connect(process_shutdown_request)
    signal_timer.start(100)
    try:
        runtime.start()
        return app.exec()
    finally:
        signal_timer.stop()
        signal.signal(signal.SIGINT, previous_sigint_handler)
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
        stop_runtime_once()
