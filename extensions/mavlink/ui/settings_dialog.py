"""Keep protocol options and the selected connection profile consistent."""

from src.models.settings import SettingsModel
from src.ui.views.settings_dialog import SettingsDialog
from .settings_schema import SETTINGS_SECTIONS


class MavlinkSettingsDialog(SettingsDialog):
    def __init__(self, settings, language, theme, parent=None):
        super().__init__(settings, language, theme, parent, sections=SETTINGS_SECTIONS)
        import pkgutil

        try:
            from pymavlink.dialects import v20

            dialects = sorted(
                module.name
                for module in pkgutil.iter_modules(v20.__path__)
                if not module.name.startswith("_")
            )
        except ImportError:
            dialects = ["ardupilotmega", "common"]
        combo = self._widgets["backend.mavlink.dialect"]
        combo.clear()
        for dialect in dialects:
            combo.addItem(dialect, dialect)
        current = settings.backend.mavlink.dialect
        if combo.findData(current) < 0:
            combo.addItem(current, current)
        combo.setCurrentIndex(combo.findData(current))

    def to_settings(self):
        payload = self._settings.to_dict()
        for section in self._sections:
            for field in section.fields:
                target = payload
                parts = field.path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = self._value_for(field.path)
        backend = payload["backend"]
        for profile in backend["mavlink_connections"]:
            if profile["identifier"] == backend["active_mavlink_connection_id"]:
                for key in ("endpoint", "baud", "heartbeat_timeout", "auto_connect"):
                    profile[key] = backend["mavlink"][key]
        return SettingsModel.from_dict(payload)
