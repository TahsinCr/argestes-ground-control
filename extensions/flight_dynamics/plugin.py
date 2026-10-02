class DynamicsExtension:
    def configure(self, registrar, context):
        registrar.visual_view(
            "view",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            region="left",
            title="Araç Dinamiği",
            legacy_key="flight",
            order=100,
            preferred_height=205,
        )

    @staticmethod
    def create(context, parent, theme, language):
        from .ui.panel import AttitudeIndicator
        from src.ui.widgets.common import PanelFrame
        from src.ui.adapters.services import QtTelemetryService
        from src.services.abstracts.telemetry import TelemetryService

        panel = PanelFrame("", "", parent)
        indicator = AttitudeIndicator(theme, panel)
        panel.body_layout.addWidget(indicator, 1)
        feed = QtTelemetryService(context.services.resolve(TelemetryService), panel)
        feed.flight_dynamics_updated.connect(indicator.update_snapshot)
        indicator.update_snapshot(feed.current)
        panel.set_theme = indicator.set_theme

        def close():
            feed.close()
            indicator.shutdown()

        panel.prepare_shutdown = close
        return panel


def create_extension():
    return DynamicsExtension()
