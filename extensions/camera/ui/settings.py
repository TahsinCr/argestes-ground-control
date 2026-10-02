"""Use the same camera editors in Settings and bottom-bar popups."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTabWidget
from .video_connections_popup import VideoConnectionsPopup
from .video_share_popup import VideoSharePopup
from extensions.camera.publishing import active_ipv4_interfaces


class CameraSettingsPage(QTabWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self._interfaces = active_ipv4_interfaces()
        self.sources = VideoConnectionsPopup(
            workspace._theme, self, language=workspace._language
        )
        self.outputs = VideoSharePopup(
            workspace._theme, self, language=workspace._language
        )
        for editor in (self.sources, self.outputs):
            editor.setWindowFlags(Qt.WindowType.Widget)
            for name in ("back_button", "all_settings_button"):
                button = getattr(editor, name, None)
                if button:
                    button.hide()
        english = workspace._language == "en"
        self.addTab(self.sources, "Cameras" if english else "Kameralar")
        self.addTab(self.outputs, "Stream / recording" if english else "Yayın / kayıt")
        self.sources.sources_saved.connect(self.save_sources)
        self.sources.source_connect_requested.connect(workspace._connect)
        self.outputs.shares_saved.connect(self.save_outputs)
        workspace.bridge.source_stats_updated.connect(self._refresh_live)
        workspace.bridge.share_states_changed.connect(self._refresh_live)
        self.refresh()

    def save_sources(self, sources, active):
        self.workspace._save_sources(sources, active)
        self.refresh()

    def save_outputs(self, outputs):
        self.workspace._save_outputs(outputs)
        self.refresh()

    def refresh(self, *_):
        runtime = self.workspace.runtime
        config = runtime.settings.current
        self.sources.show_sources(
            config.sources,
            active_identifier=config.active_source_id,
            connected=runtime.video.connected,
            source_stats=runtime.video.source_stats,
        )
        self.outputs.show_shares(
            config.outputs,
            runtime.video.share_states,
            network_interfaces=self._interfaces,
            sources=config.sources,
            source_stats=runtime.video.source_stats,
            active_source_id=config.active_source_id,
        )

    def _refresh_live(self, *_):
        if self.isVisible():
            runtime = self.workspace.runtime
            config = runtime.settings.current
            self.sources.update_source_stats(runtime.video.source_stats)
            self.outputs.update_sources(
                config.sources, runtime.video.source_stats, config.active_source_id
            )
            self.outputs.update_states(runtime.video.share_states)

    def showEvent(self, event):
        super().showEvent(event)
        self._interfaces = active_ipv4_interfaces()
        self.refresh()

    def prepare_shutdown(self):
        self.workspace.bridge.source_stats_updated.disconnect(self._refresh_live)
        self.workspace.bridge.share_states_changed.disconnect(self._refresh_live)
