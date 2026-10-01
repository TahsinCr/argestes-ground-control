"""Own a normalized data channel without knowing its protocol implementation."""

from src.core.abstracts import Service
from src.models.telemetry import TelemetrySnapshot
import threading


class TelemetryRuntime(Service):
    def __init__(self, source, telemetry, settings, logging):
        self._provider = source
        self._telemetry = telemetry
        self._settings = settings
        self._logging = logging
        self._feed = None
        self._subscription = None
        self._generation = 0
        self._lock = threading.RLock()

    def start(self):
        if self._feed is not None:
            return
        feed = self._provider.create_telemetry(self._settings, self._logging)
        with self._lock:
            self._generation += 1
            generation = self._generation

        def forward(snapshot):
            with self._lock:
                if self._generation == generation:
                    self._telemetry.push(snapshot)

        subscription = feed.updated.subscribe(forward)
        try:
            feed.start()
            self._telemetry.push(feed.current)
        except Exception:
            with self._lock:
                self._generation += 1
            subscription.cancel()
            feed.stop()
            self._telemetry.push(TelemetrySnapshot.zero())
            raise
        self._feed = feed
        self._subscription = subscription

    def stop(self):
        with self._lock:
            self._generation += 1
            feed, subscription = self._feed, self._subscription
            self._feed = self._subscription = None
        if subscription is not None:
            subscription.cancel()
        try:
            if feed is not None:
                feed.stop()
        finally:
            self._telemetry.push(TelemetrySnapshot.zero())
