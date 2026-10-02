"""Extension-owned preferences adapted to the established map widget API."""

from PyQt6.QtWidgets import QFormLayout, QComboBox, QSpinBox, QDialogButtonBox
from src.ui.adapters.services import QtSettingsService
from src.ui.i18n import t
from PyQt6.QtCore import pyqtSignal


class MapSettings(QtSettingsService):
    cache_cleared = pyqtSignal()

    def __init__(self, backend, parent=None, cache=None):
        super().__init__(backend, parent)
        self.cache = cache
        self._subscribe(
            backend.extensions_changed,
            lambda identifier: (
                self.settings_changed.emit(self.current)
                if identifier == "map_autonomy"
                else None
            ),
        )

    @property
    def current(self):
        settings = self.backend.current
        config = self.backend.extension_config("map_autonomy")
        if self.cache is not None and not settings.backend.cache.path:
            settings.backend.cache.path = str(self.cache.cache_path)
        for key in (
            "map_layer",
            "map_center_min_zoom",
            "vehicle_trail_limit",
            "contact_trail_limit",
        ):
            if key in config:
                setattr(settings.ui, key, config[key])
        settings.ui.__post_init__()
        return settings

    def patch(self, **changes):
        self._start_worker(
            lambda: self.backend.update_extension("map_autonomy", config=changes),
            name="MapSettings",
        )

    def clear_cache(self):
        if self.cache is not None:

            def clear():
                self.cache.clear()
                self._post(self.cache_cleared.emit)

            self._start_worker(clear, name="MapCacheClear")


def configure(settings, anchor, cache=None):
    from PyQt6.QtWidgets import QLineEdit, QCheckBox, QPushButton

    current = MapSettings(settings, cache=cache)
    language = settings.current.ui.language
    from src.ui.widgets.settings_page import SettingsPageDialog

    dialog = SettingsPageDialog(anchor)
    dialog.setWindowTitle(t("extension.map_autonomy.name", language))
    form = QFormLayout(dialog)
    layer = QComboBox(dialog)
    layer.addItem(t("map.layer.vector", language), "vector")
    layer.addItem(t("map.layer.satellite", language), "satellite")
    layer.setCurrentIndex(layer.findData(current.current.ui.map_layer))
    form.addRow(t("map.settings.layer", language), layer)
    fields = {}
    for key, low, high in (
        ("map_center_min_zoom", 1, 22),
        ("vehicle_trail_limit", 1, 10000),
        ("contact_trail_limit", 1, 10000),
    ):
        field = QSpinBox(dialog)
        field.setRange(low, high)
        field.setValue(getattr(current.current.ui, key))
        fields[key] = field
        form.addRow(t("map.settings." + key, language), field)
    cache_enabled = QCheckBox(dialog)
    cache_enabled.setChecked(settings.current.backend.cache.enabled)
    form.addRow(t("dialog.settings.cache", language), cache_enabled)
    cache_limit = QSpinBox(dialog)
    cache_limit.setRange(256, 8192)
    cache_limit.setValue(settings.current.backend.cache.limit_mb)
    form.addRow(t("dialog.settings.cache_limit", language), cache_limit)
    cache_path = QLineEdit(
        settings.current.backend.cache.path or str(cache.cache_path if cache else ""),
        dialog,
    )
    form.addRow(t("dialog.settings.cache_path", language), cache_path)
    clear = QPushButton(t("dialog.settings.clear_cache", language), dialog)
    clear.clicked.connect(current.clear_cache)
    form.addRow(clear)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)

    def apply():
        values = {key: field.value() for key, field in fields.items()}
        values["map_layer"] = layer.currentData()
        values["cache"] = {
            "enabled": cache_enabled.isChecked(),
            "limit_mb": cache_limit.value(),
            "path": cache_path.text().strip(),
        }
        current._start_worker(
            lambda: settings.update_extension("map_autonomy", config=values),
            name="MapPreferences",
        )

    def close():
        current.close()
        current.deleteLater()

    dialog.prepare_shutdown = close
    from src.ui.widgets.settings_page import embed_dialog

    return embed_dialog(dialog, commit=apply)
