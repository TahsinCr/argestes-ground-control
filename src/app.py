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

from src.container import create_container
from src.services.runtime import RuntimeServices
from src.ui.assets import application_icon
from src.ui.adapters.runtime import QtRuntimeServices
from src.ui.container import UiContainer
from src.ui.i18n import t
from src.ui.themes.manager import apply_theme
from src.ui.views.main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Kırlangıç Team YKİ")
    app.setOrganizationName("Kırlangıç Team")

    base_dir = Path(__file__).resolve().parent.parent
    app.setWindowIcon(application_icon(base_dir))
    backend_container = create_container(base_dir)
    backend_runtime = backend_container.resolve(RuntimeServices)
    app.setApplicationName(
        t("app.title", backend_runtime.settings_service.current.language)
    )
    theme = apply_theme(app, backend_runtime.settings_service.current.ui.theme)
    ui_container = UiContainer(
        backend_container.container,
        base_dir=base_dir,
        theme=theme,
    )
    runtime = ui_container.resolve(QtRuntimeServices)
    window = ui_container.resolve(MainWindow)
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
