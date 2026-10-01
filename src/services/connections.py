"""Stable, protocol-neutral facade for an optional connection extension."""
from src.core.events import EventBus
from src.models.connections import ConnectionStatus
from src.services.abstracts.connections import ConnectionControlService


class ConnectionHub(ConnectionControlService):
    def __init__(self):
        self._controller = None
        self._subscription = None
        self._changed = EventBus[ConnectionStatus]()

    @property
    def changed(self):
        return self._changed

    def bind(self, controller):
        if self._subscription is not None:
            self._subscription.cancel()
        self._controller = controller
        self._subscription = controller.changed.subscribe(self._changed.publish) if controller else None
        for channel in ("telemetry", "autopilot", "gps"):
            self._changed.publish(self.status(channel))

    def start(self):
        pass

    def stop(self):
        pass

    def status(self, channel):
        if self._controller is not None:
            return self._controller.status(channel)
        return ConnectionStatus(channel, False, "Connection extension inactive", can_disconnect=False)

    def _active(self):
        if self._controller is None:
            raise RuntimeError("No active connection extension")
        return self._controller

    def reconnect(self, channel, options):
        return self._active().reconnect(channel, options)

    def disconnect(self, channel):
        return self._active().disconnect(channel)

    def repair_serial_access(self, channel):
        return self._active().repair_serial_access(channel)
