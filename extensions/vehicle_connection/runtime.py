"""Own transport startup/recovery independently of the selected protocol."""

from src.core.abstracts import Service


class ConnectionRuntime(Service):
    def __init__(self, source, binding):
        self._source = source
        self._binding = binding
        self._controller = None

    def start(self):
        if self._controller is not None:
            return
        controller = self._source.create_connection()
        self._binding.bind(controller)
        try:
            controller.start()
        except Exception:
            self._binding.bind(None)
            controller.stop()
            raise
        self._controller = controller

    def stop(self):
        controller, self._controller = self._controller, None
        self._binding.bind(None)
        if controller is not None:
            controller.stop()
