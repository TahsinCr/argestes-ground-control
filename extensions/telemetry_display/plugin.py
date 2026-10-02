class TelemetryDisplayExtension:
    def configure(self, registrar, context):
        self.context = context
        registrar.visual_view(
            "view",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            region="left",
            title="Araç Telemetrisi",
            legacy_key="telemetry",
            order=200,
            preferred_height=280,
        )
        registrar.settings_page(lambda anchor: self.settings(context, anchor))

    @staticmethod
    def create(context, parent, theme, language):
        from .ui.panel import TelemetryPanel
        from src.ui.adapters.services import QtTelemetryService
        from src.services.abstracts.telemetry import TelemetryService
        from src.services.settings import SettingsService

        panel = TelemetryPanel("", "", theme, parent)
        settings = context.services.resolve(SettingsService)
        feed = QtTelemetryService(context.services.resolve(TelemetryService), panel)
        feed.telemetry_updated.connect(panel.update_snapshot)
        panel.update_snapshot(feed.current)

        def configure(_=None):
            visible = settings.extension_config("telemetry_display").get("fields")
            panel.set_visible_fields(visible)

        subscription = settings.extensions_changed.subscribe(
            lambda key: feed._post(configure) if key == "telemetry_display" else None
        )
        configure()
        panel.prepare_shutdown = lambda: (subscription.cancel(), feed.close())
        return panel

    @staticmethod
    def settings(context, anchor):
        from PyQt6.QtWidgets import QVBoxLayout, QCheckBox, QDialogButtonBox
        from .ui.panel import TelemetryPanel
        from src.ui.i18n import t
        from src.services.settings import SettingsService

        settings = context.services.resolve(SettingsService)
        fields = [field for group in TelemetryPanel.SECTION_SPECS for field in group[1]]
        selected = settings.extension_config("telemetry_display").get(
            "fields", [item[0] for item in fields]
        )
        from src.ui.widgets.settings_page import SettingsPageDialog, embed_dialog

        dialog = SettingsPageDialog(anchor)
        dialog.setWindowTitle("Telemetri alanları")
        layout = QVBoxLayout(dialog)
        controls = {}
        for key, label in fields:
            checkbox = QCheckBox(t(label, settings.current.ui.language), dialog)
            checkbox.setChecked(key in selected)
            controls[key] = checkbox
            layout.addWidget(checkbox)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def apply():
            settings.update_extension(
                "telemetry_display",
                config={
                    "fields": [key for key, box in controls.items() if box.isChecked()]
                },
            )

        return embed_dialog(dialog, commit=apply)


def create_extension():
    return TelemetryDisplayExtension()
