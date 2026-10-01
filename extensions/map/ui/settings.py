"""Extension-owned preferences adapted to the established map widget API."""
from PyQt6.QtWidgets import QDialog, QFormLayout, QComboBox, QSpinBox, QDialogButtonBox
from src.ui.adapters.services import QtSettingsService
from src.ui.i18n import t


class MapSettings(QtSettingsService):
    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self._subscribe(backend.extensions_changed,
                        lambda identifier: self.settings_changed.emit(self.current) if identifier == "map" else None)

    @property
    def current(self):
        settings = self.backend.current
        config = self.backend.extension_config("map")
        for key in ("map_layer", "map_center_min_zoom", "vehicle_trail_limit", "contact_trail_limit"):
            if key in config:
                setattr(settings.ui, key, config[key])
        settings.ui.__post_init__()
        return settings

    def patch(self, **changes):
        self._start_worker(lambda: self.backend.update_extension("map", config=changes), name="MapSettings")


def configure(settings, anchor):
    current = MapSettings(settings)
    language = settings.current.ui.language
    dialog = QDialog(anchor)
    dialog.setWindowTitle(t("extension.map.name", language))
    form = QFormLayout(dialog)
    layer = QComboBox(dialog)
    layer.addItem(t("map.layer.vector", language), "vector")
    layer.addItem(t("map.layer.satellite", language), "satellite")
    layer.setCurrentIndex(layer.findData(current.current.ui.map_layer))
    form.addRow(t("map.settings.layer", language), layer)
    fields = {}
    for key, low, high in (("map_center_min_zoom", 1, 22), ("vehicle_trail_limit", 1, 10000),
                            ("contact_trail_limit", 1, 10000)):
        field = QSpinBox(dialog)
        field.setRange(low, high)
        field.setValue(getattr(current.current.ui, key))
        fields[key] = field
        form.addRow(t("map.settings." + key, language), field)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = {key: field.value() for key, field in fields.items()}
            values["map_layer"] = layer.currentData()
            settings.update_extension("map", config=values)
    finally:
        current.close()
        current.deleteLater()
        dialog.deleteLater()
