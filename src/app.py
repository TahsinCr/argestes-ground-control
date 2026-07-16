import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from src.services.runtime import create_runtime_services
from src.themes.manager import apply_theme
from src.views.main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Kuzey Takımı YKİ")
    app.setOrganizationName("Kuzey Takımı")

    base_dir = Path(__file__).resolve().parent.parent
    runtime = create_runtime_services(base_dir)
    theme = apply_theme(app, runtime.settings_service.current.theme)

    window = MainWindow(
        log_service=runtime.log_service,
        settings_service=runtime.settings_service,
        telemetry_service=runtime.telemetry_service,
        mission_service=runtime.mission_service,
        command_service=runtime.command_service,
        video_service=runtime.video_service,
        base_dir=base_dir,
        theme=theme,
    )
    window.show()

    runtime.start()

    return app.exec()
