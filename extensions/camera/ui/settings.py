"""Use the same camera editors in Settings and bottom-bar popups."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTabWidget
from .video_connections_popup import VideoConnectionsPopup
from .video_share_popup import VideoSharePopup
from extensions.camera.publishing import active_ipv4_interfaces


class CameraSettingsPage(QTabWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsTabs")
        self.setUsesScrollButtons(True)
        self.workspace = workspace
        self._interfaces = active_ipv4_interfaces()
        self.sources = VideoConnectionsPopup(
            workspace._theme, self, language=workspace._language
        )
        self.streaming = VideoSharePopup(
            workspace._theme,
            self,
            language=workspace._language,
            output_type_filter="stream",
        )
        self.recording = VideoSharePopup(
            workspace._theme,
            self,
            language=workspace._language,
            clock_references=workspace.runtime.clock_references,
            show_clock_selector=True,
            output_type_filter="record",
        )
        for editor in (self.sources, self.streaming, self.recording):
            editor.setWindowFlags(Qt.WindowType.Widget)
            editor.setMinimumSize(0, 0)
            editor.setMaximumSize(16777215, 16777215)
            for name in ("back_button", "all_settings_button"):
                button = getattr(editor, name, None)
                if button:
                    button.hide()
        english = workspace._language == "en"
        self.addTab(self.sources, "Camera" if english else "Kamera")
        self.addTab(self.streaming, "Streaming" if english else "Yayın")
        self.addTab(self.recording, "Recording" if english else "Kayıt")
        self.sources.sources_saved.connect(self.save_sources)
        self.sources.source_connect_requested.connect(workspace._connect)
        self.streaming.shares_saved.connect(self.save_outputs)
        self.recording.shares_saved.connect(self.save_outputs)
        self.recording.clock_reference_changed.connect(
            workspace.runtime.save_clock_reference
        )
        workspace.bridge.source_stats_updated.connect(self._refresh_live)
        workspace.bridge.share_states_changed.connect(self._refresh_live)
        self.refresh()

    def save_sources(self, sources, active):
        self.workspace._save_sources(sources, active)
        self.refresh()

    def set_theme(self, theme):
        self.sources.set_theme(theme)
        self.streaming.set_theme(theme)
        self.recording.set_theme(theme)

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
        for editor in (self.streaming, self.recording):
            editor.show_shares(
                config.outputs,
                runtime.video.share_states,
                network_interfaces=self._interfaces,
                sources=config.sources,
                source_stats=runtime.video.source_stats,
                active_source_id=config.active_source_id,
            )
        self.recording.set_clock_reference(config.clock_reference_id)

    def _refresh_live(self, *_):
        if self.isVisible():
            runtime = self.workspace.runtime
            config = runtime.settings.current
            self.sources.update_source_stats(runtime.video.source_stats)
            for editor in (self.streaming, self.recording):
                editor.update_sources(
                    config.sources, runtime.video.source_stats, config.active_source_id
                )
                editor.update_states(runtime.video.share_states)

    def showEvent(self, event):
        super().showEvent(event)
        self._interfaces = active_ipv4_interfaces()
        self.refresh()

    def prepare_shutdown(self):
        self.workspace.bridge.source_stats_updated.disconnect(self._refresh_live)
        self.workspace.bridge.share_states_changed.disconnect(self._refresh_live)
