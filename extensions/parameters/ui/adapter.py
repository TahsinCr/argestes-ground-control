from __future__ import annotations
import threading
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer
from src.ui.adapters.services import _QtAdapter
from src.extension.api.parameters import ParameterService


class QtParameterService(_QtAdapter):
    parameters_changed = pyqtSignal(object)
    write_finished = pyqtSignal(object, object)
    _UI_UPDATE_INTERVAL_MS = 100

    def __init__(
        self, backend: ParameterService, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self._parameter_lock = threading.Lock()
        self._latest_parameters = None
        self._parameter_update_scheduled = False
        self._subscriptions.append(backend.changed.subscribe(self._capture_parameters))
        self._parameter_timer = QTimer(self)
        self._parameter_timer.setSingleShot(True)
        self._parameter_timer.setInterval(self._UI_UPDATE_INTERVAL_MS)
        self._parameter_timer.timeout.connect(self._emit_latest_parameters)

    def _capture_parameters(self, parameters) -> None:
        should_schedule = False
        with self._parameter_lock:
            self._latest_parameters = parameters
            if not self._parameter_update_scheduled:
                self._parameter_update_scheduled = True
                should_schedule = True
        if should_schedule:
            self._post(self._start_parameter_timer)

    @pyqtSlot()
    def _start_parameter_timer(self) -> None:
        if not self._closed and not self._parameter_timer.isActive():
            self._parameter_timer.start()

    @pyqtSlot()
    def _emit_latest_parameters(self) -> None:
        with self._parameter_lock:
            parameters = self._latest_parameters
            self._latest_parameters = None
            self._parameter_update_scheduled = False
        if parameters is not None:
            self.parameters_changed.emit(parameters)

    def close(self) -> None:
        self._parameter_timer.stop()
        with self._parameter_lock:
            self._latest_parameters = None
            self._parameter_update_scheduled = False
        super().close()

    @property
    def current(self):
        return self.backend.current

    def replace(self, parameters) -> None:
        self.backend.replace(parameters)

    def upsert(self, parameter) -> None:
        self.backend.upsert(parameter)

    def update_values(self, changes: dict[str, object]) -> threading.Thread:
        return self._start_worker(
            self._update_values,
            name="QtParameterWrite",
            args=(changes,),
        )

    def refresh(self) -> threading.Thread:
        return self._start_worker(
            self._refresh,
            name="QtParameterRefresh",
        )

    def _update_values(self, changes: dict[str, object]) -> None:
        try:
            self.backend.update_values(changes)
        except Exception as exc:
            error = exc
        else:
            error = None
        if not self._closed:
            self.write_finished.emit(dict(changes), error)

    def _refresh(self) -> None:
        try:
            self.backend.refresh()
        except Exception:
            return
