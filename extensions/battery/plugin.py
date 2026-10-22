class BatteryExtension:
    def configure(self, registrar, context):
        from dataclasses import asdict
        from src.services.settings import SettingsService
        from src.models.settings import SettingsModel

        settings = context.services.resolve(SettingsService)

        def restore(model, config):
            payload = model.to_dict()
            for key in ("battery_alarm", "battery_telemetry"):
                if key in config:
                    payload["backend"][key] = config[key]
            return SettingsModel.from_dict(payload)

        settings.bind_extension_settings(
            "battery",
            lambda model: {
                "battery_alarm": asdict(model.backend.battery_alarm),
                "battery_telemetry": asdict(model.backend.battery_telemetry),
            },
            restore,
        )
        self.context = context
        registrar.status_widget(
            "meter",
            lambda parent, theme, language: self.create(
                context, parent, theme, language
            ),
            placement="metrics",
        )
        registrar.settings_page(lambda parent: self._settings_page(context, parent))

    def start(self, context):
        from concurrent.futures import ThreadPoolExecutor
        from .alarm import BatteryAlarmService
        from src.extension.api.telemetry import TelemetryPipeline
        from src.services.abstracts.commands import CommandService
        from src.services.abstracts.telemetry import TelemetryService
        from src.services.logging_service import LoggingService
        from src.services.settings import SettingsService
        from .processor import BatteryProcessor

        settings = context.services.resolve(SettingsService)
        commands = context.services.resolve(CommandService)
        telemetry = context.services.resolve(TelemetryService)
        self._failsafe_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="BatteryFailsafe",
        )
        self.alarm = BatteryAlarmService(
            context.services.resolve(LoggingService).child("BATTERY"),
            lambda mode: self._failsafe_executor.submit(
                commands.issue_command,
                "APPLY_MODE",
                {"mode": mode, "battery_failsafe": True},
                source="battery-failsafe",
            ),
        )
        self._alarm_settings = settings.current.backend.battery_alarm
        self._telemetry_subscription = telemetry.updated.subscribe(
            lambda snapshot: self.alarm.evaluate(
                snapshot,
                self._alarm_settings,
            )
        )
        self.processor = BatteryProcessor(settings.current.backend.battery_telemetry)

        def settings_changed(model):
            self._alarm_settings = model.backend.battery_alarm
            self.processor.configure(model.backend.battery_telemetry)

        self.subscription = settings.changed.subscribe(settings_changed)
        self.pipeline = context.services.resolve(TelemetryPipeline)
        self.pipeline.add_processor("battery.estimation", self.processor)

    def stop(self):
        alarm = getattr(self, "alarm", None)
        if alarm is not None:
            alarm.close()
        telemetry_subscription = getattr(self, "_telemetry_subscription", None)
        if telemetry_subscription is not None:
            telemetry_subscription.cancel()
            self._telemetry_subscription = None
        if hasattr(self, "subscription"):
            self.subscription.cancel()
            self.pipeline.remove_processor("battery.estimation")
        executor = getattr(self, "_failsafe_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
            self._failsafe_executor = None

    def create(self, context, parent, theme, language):
        from .ui.meter import BatteryMeter
        from src.ui.adapters.services import QtTelemetryService
        from src.services.abstracts.telemetry import TelemetryService
        from src.ui.i18n import t

        meter = BatteryMeter(t("status.battery", language), parent)
        meter.set_theme(theme)
        meter.set_language(language)
        feed = QtTelemetryService(context.services.resolve(TelemetryService), meter)

        def update(snapshot):
            meter.set_battery(
                snapshot.battery_percent,
                snapshot.battery_voltage,
                snapshot.battery_current_amps,
                snapshot.battery_consumed_mah,
            )
            alarm = getattr(self, "alarm", None)
            meter.set_alarm(alarm.active if alarm is not None else False)

        feed.telemetry_updated.connect(update)
        update(feed.current)
        meter.prepare_shutdown = feed.close
        return meter

    @staticmethod
    def _settings_page(context, anchor):
        from .ui.settings_popup import BatterySettingsPopup
        from src.services.settings import SettingsService
        from src.services.abstracts.telemetry import TelemetryService
        from src.models.settings import SettingsModel
        from src.ui.themes.palette import palette_for

        settings = context.services.resolve(SettingsService)
        current = settings.current
        dialog = BatterySettingsPopup(
            palette_for(current.ui.theme), anchor, language=current.ui.language
        )
        dialog.show_settings(
            current, context.services.resolve(TelemetryService).current
        )

        def save(values):
            payload = settings.current.to_dict()
            for key, value in values.items():
                for section in ("battery_alarm", "battery_telemetry"):
                    if key in payload["backend"][section]:
                        payload["backend"][section][key] = value
            settings.save(SettingsModel.from_dict(payload))
            dialog.set_result(True, "Ayarlar kaydedildi")

        dialog.save_requested.connect(save)
        from src.ui.widgets.settings_page import embed_dialog

        dialog.all_settings_button.hide()
        return embed_dialog(dialog)


def create_extension():
    return BatteryExtension()
