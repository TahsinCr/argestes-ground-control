"""Stable, protocol-neutral facade for an optional connection extension."""

from src.core.events import EventBus
from src.models.connections import ConnectionPhase, ConnectionStatus
from src.services.abstracts.connections import ConnectionControlService
import threading


class ConnectionHub(ConnectionControlService):
    def __init__(self):
        self._controller = None
        self._subscription = None
        self._changed = EventBus[ConnectionStatus]()
        self._lock = threading.RLock()
        self._generation = 0

    @property
    def changed(self):
        return self._changed

    def bind(self, controller):
        with self._lock:
            self._generation += 1
            generation = self._generation
            if self._subscription is not None:
                self._subscription.cancel()
            self._controller = controller

            def forward(status):
                with self._lock:
                    if generation == self._generation:
                        self._changed.publish(status)

            self._subscription = (
                controller.changed.subscribe(forward) if controller else None
            )

    def start(self):
        pass

    def stop(self):
        self.bind(None)

    def status(self, channel):
        if self._controller is not None:
            return self._controller.status(channel)
        return ConnectionStatus(
            channel,
            False,
            ConnectionPhase.DISCONNECTED,
            "Connection extension inactive",
            can_disconnect=False,
        )

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
