"""Archived camera wall with extension-owned controls and status widgets."""

from __future__ import annotations

import threading
from dataclasses import replace

from PyQt6.QtCore import QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QWidget

from src.ui.i18n import t
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import StatusChip

from extensions.camera.models import VideoFrame, VideoShareConfig, VideoShareState, VideoSourceConfig, VideoStreamStats
from extensions.camera.publishing import active_ipv4_interfaces
from extensions.camera.runtime import CameraRuntime
from .video import VideoPanel
from .video_connections_popup import VideoConnectionsPopup
from .video_share_popup import VideoSharePopup


class CameraFrameBridge(QWidget):
    """Latest-frame-only bridge: capture never queues an unbounded Qt frame backlog."""

    frame_ready = pyqtSignal(QImage)
    source_frame_ready = pyqtSignal(str, QImage)
    stats_updated = pyqtSignal(object)
    source_stats_updated = pyqtSignal(object)
    share_states_changed = pyqtSignal(object)

    def __init__(self, runtime: CameraRuntime, parent: QWidget) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._frame: VideoFrame | None = None
        self._source_frames: dict[str, VideoFrame] = {}
        video = runtime.video
        self._subscriptions = [
            video.frames.subscribe(self._capture_frame),
            video.source_frames.subscribe(self._capture_source_frame),
            video.stats_changed.subscribe(self.stats_updated.emit),
            video.source_stats_changed.subscribe(self.source_stats_updated.emit),
            video.share_states_changed.subscribe(self.share_states_changed.emit),
        ]
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def _capture_frame(self, frame: VideoFrame) -> None:
        with self._lock:
            self._frame = frame

    def _capture_source_frame(self, item) -> None:
        with self._lock:
            self._source_frames[item.source_id] = item.frame

    def _flush(self) -> None:
        with self._lock:
            frame, self._frame = self._frame, None
            source_frames, self._source_frames = self._source_frames, {}
        if frame is not None:
            self.frame_ready.emit(self._image(frame))
        for identifier, value in source_frames.items():
            self.source_frame_ready.emit(identifier, self._image(value))

    @staticmethod
    def _image(frame: VideoFrame) -> QImage:
        formats = {
            "rgba8888": QImage.Format.Format_RGBA8888,
            "rgb888": QImage.Format.Format_RGB888,
            "bgr888": QImage.Format.Format_BGR888,
            "gray8": QImage.Format.Format_Grayscale8,
        }
        pixel_size = {"rgba8888": 4, "rgb888": 3, "bgr888": 3, "gray8": 1}
        stride = frame.stride or frame.width * pixel_size[frame.pixel_format]
        return QImage(frame.data, frame.width, frame.height, stride,
                      formats[frame.pixel_format]).copy()

    def stop(self) -> None:
        self._timer.stop()
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()


class CameraWorkspace(VideoPanel):
    reconnect_completed = pyqtSignal(bool, str)

    def __init__(self, runtime: CameraRuntime, theme: ThemePalette,
                 language: str, parent: QWidget | None = None) -> None:
        self.runtime = runtime
        super().__init__("", "", theme=theme, language=language, parent=parent)
        self._language = language
        self._closed = False
        self.bridge = CameraFrameBridge(runtime, self)
        self.bridge.frame_ready.connect(self.set_frame)
        self.bridge.source_frame_ready.connect(self.set_source_frame)
        self.bridge.stats_updated.connect(self.set_stats)
        self.bridge.source_stats_updated.connect(self._source_stats)
        self.bridge.share_states_changed.connect(self._share_states)
        self.sources_popup = VideoConnectionsPopup(theme, self, language=language)
        self.shares_popup = VideoSharePopup(theme, self, language=language)
        self.sources_popup.sources_saved.connect(self._save_sources)
        self.sources_popup.source_connect_requested.connect(self._connect)
        self.shares_popup.shares_saved.connect(self._save_outputs)
        self.reconnect_requested.connect(lambda: self._connect(self.runtime.settings.current.active_source_id))
        self.recording_requested.connect(self._record_all)
        self.source_selected.connect(self._select_source)
        self.aim_changed.connect(self._save_aim)
        self.reconnect_completed.connect(self._reconnect_completed)
        self._sync_settings()

    def _sync_settings(self) -> None:
        config = self.runtime.settings.current
        self.set_sources(config.sources, config.active_source_id)
        self.set_recording_enabled(any(item.output_type == "record" and item.enabled
                                       for item in config.outputs))
        for item in self.runtime.video.source_stats:
            self.set_source_stats(item)
        self.set_stats(self.runtime.video.stats)

    def _save_sources(self, sources: tuple[VideoSourceConfig, ...], active_id: str) -> None:
        try:
            self.runtime.save_sources(sources, active_id)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Camera", str(exc))
            return
        self._sync_settings()

    def _select_source(self, identifier: str) -> None:
        config = self.runtime.settings.current
        self._save_sources(config.sources, identifier)

    def _save_aim(self, identifier: str, enabled: bool, x: float, y: float) -> None:
        config = self.runtime.settings.current
        sources = tuple(replace(item, aim_enabled=enabled, aim_x=x, aim_y=y)
                        if item.identifier == identifier else item for item in config.sources)
        self._save_sources(sources, config.active_source_id)

    def _save_outputs(self, outputs: tuple[VideoShareConfig, ...]) -> None:
        try:
            self.runtime.save_outputs(outputs)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Camera", str(exc))
            return
        self._sync_settings()

    def _record_all(self, enabled: bool) -> None:
        self.set_recording_busy(True)
        try:
            self.runtime.record_all(enabled)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Camera", str(exc))
        finally:
            self._sync_settings()
            self.set_recording_busy(False)

    def _connect(self, identifier: str) -> None:
        self.set_reconnect_busy(True)
        try:
            future = self.runtime.connect_source(identifier)
        except (ValueError, RuntimeError) as exc:
            self.reconnect_completed.emit(False, str(exc))
            return
        self._sync_settings()

        def completed(result) -> None:
            if self._closed:
                return
            try:
                result.result()
            except Exception as exc:
                self.reconnect_completed.emit(False, str(exc))
            else:
                self.reconnect_completed.emit(True, "")

        future.add_done_callback(completed)

    def _reconnect_completed(self, success: bool, detail: str) -> None:
        self.set_reconnect_busy(False)
        if not success:
            QMessageBox.warning(self, "Camera", detail)

    def _source_stats(self, item) -> None:
        self.set_source_stats(item)
        if self.sources_popup.isVisible():
            self.sources_popup.update_source_stats(self.runtime.video.source_stats)
        if self.shares_popup.isVisible():
            config = self.runtime.settings.current
            self.shares_popup.update_sources(config.sources, self.runtime.video.source_stats,
                                             config.active_source_id)

    def _share_states(self, states: tuple[VideoShareState, ...]) -> None:
        if self.shares_popup.isVisible():
            self.shares_popup.update_states(states)

    @staticmethod
    def _place_popup(popup, anchor: QWidget) -> None:
        popup.adjustSize()
        origin = anchor.mapToGlobal(QPoint(0, 0))
        screen = anchor.screen().availableGeometry()
        popup.move(min(max(screen.left() + 6, origin.x()),
                       screen.right() - popup.width() - 6),
                   max(screen.top() + 6, origin.y() - popup.height() - 7))
        popup.show()
        popup.raise_()

    def show_sources_popup(self, anchor: QWidget) -> None:
        config = self.runtime.settings.current
        self.sources_popup.show_sources(config.sources, active_identifier=config.active_source_id,
                                        connected=self.runtime.video.connected,
                                        source_stats=self.runtime.video.source_stats)
        self._place_popup(self.sources_popup, anchor)

    def show_outputs_popup(self, anchor: QWidget) -> None:
        config = self.runtime.settings.current
        self.shares_popup.show_shares(config.outputs, self.runtime.video.share_states,
                                      network_interfaces=active_ipv4_interfaces(),
                                      sources=config.sources,
                                      source_stats=self.runtime.video.source_stats,
                                      active_source_id=config.active_source_id)
        self._place_popup(self.shares_popup, anchor)
        QTimer.singleShot(180, lambda: self.shares_popup.isVisible() and
                          self.shares_popup.show_shares(
                              config.outputs, self.runtime.video.share_states,
                              network_interfaces=active_ipv4_interfaces(),
                              sources=config.sources, source_stats=self.runtime.video.source_stats,
                              active_source_id=config.active_source_id))

    def set_theme(self, theme: ThemePalette) -> None:
        super().set_theme(theme)
        if hasattr(self, "sources_popup"):
            self.sources_popup.set_theme(theme)
            self.shares_popup.set_theme(theme)

    def set_language(self, language: str) -> None:
        super().set_language(language)
        if hasattr(self, "sources_popup"):
            self.sources_popup.set_language(language)
            self.shares_popup.set_language(language)

    def prepare_shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.sources_popup.hide()
        self.shares_popup.hide()
        self.bridge.stop()

    def closeEvent(self, event) -> None:
        self.prepare_shutdown()
        super().closeEvent(event)


class CameraStatusWidget(QWidget):
    def __init__(self, workspace: CameraWorkspace, theme: ThemePalette,
                 language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.video_chip = StatusChip("VIDEO", self)
        self.share_chip = StatusChip("VIDEO SHARE", self)
        for chip in (self.video_chip, self.share_chip):
            chip.set_theme(theme)
            chip.set_state("danger")
            layout.addWidget(chip)
        self.video_chip.clicked.connect(lambda: workspace.show_sources_popup(self.video_chip))
        self.share_chip.clicked.connect(lambda: workspace.show_outputs_popup(self.share_chip))
        workspace.bridge.stats_updated.connect(self._stats)
        workspace.bridge.share_states_changed.connect(self._shares)
        self.set_language(language)
        self._stats(workspace.runtime.video.stats)
        self._shares(workspace.runtime.video.share_states)

    def _stats(self, stats: VideoStreamStats) -> None:
        self.video_chip.set_state("success" if stats.connected else "danger")
        config = self.workspace.runtime.settings.current
        source = next((item for item in config.sources if item.identifier == config.active_source_id), None)
        self.video_chip.setToolTip(f"{source.name if source else 'Video Link'}\n"
                                   f"{source.source_uri if source else ''}")

    def _shares(self, states: tuple[VideoShareState, ...]) -> None:
        self.share_chip.set_state("success" if any(item.active for item in states) else "danger")
        self.share_chip.setToolTip("\n".join(f"{item.identifier[:8]}: {item.detail}"
                                           for item in states if item.detail))

    def set_theme(self, theme: ThemePalette) -> None:
        self.video_chip.set_theme(theme)
        self.share_chip.set_theme(theme)

    def set_language(self, language: str) -> None:
        self.video_chip.set_label("VIDEO")
        self.share_chip.set_label("VIDEO SHARE")
