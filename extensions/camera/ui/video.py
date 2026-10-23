from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from extensions.camera.models import VideoStreamStats
from extensions.camera.models import VideoSourceConfig
from src.ui.i18n import t
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame


class VideoViewport(QWidget):
    view_mode_changed = pyqtSignal(str)
    reconnect_requested = pyqtSignal()
    recording_requested = pyqtSignal(bool)
    source_menu_requested = pyqtSignal(object)
    clicked = pyqtSignal()

    def __init__(
        self,
        theme: ThemePalette,
        language: str,
        parent: QWidget | None = None,
        *,
        controls: bool = True,
    ) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._status = "NO SIGNAL"
        self._theme = theme
        self._language = language
        self._display_mode = "fit"
        self._controls = controls
        self._controls_hosted_externally = False
        self._compact_overlays = False
        self._current_stats = VideoStreamStats()
        self._source_name = ""
        self.setMinimumHeight(180 if controls else 120)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.stats_label = QLabel(self)
        self.stats_label.setObjectName("VideoStatsOverlay")
        self.stats_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self.stats_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.source_label = QLabel(self)
        self.source_label.setObjectName("VideoSourceOverlay")
        self.source_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        self.source_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.source_label.installEventFilter(self)
        self.source_label.customContextMenuRequested.connect(
            lambda position: self.source_menu_requested.emit(
                self.source_label.mapToGlobal(position)
            )
        )

        self.mode_toolbar = QFrame(self)
        self.mode_toolbar.setObjectName("VideoModeToolbar")
        toolbar_layout = QHBoxLayout(self.mode_toolbar)
        toolbar_layout.setContentsMargins(4, 4, 4, 4)
        toolbar_layout.setSpacing(3)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.fit_button = self._mode_button("▣", "fit")
        self.fill_button = self._mode_button("⛶", "fill")
        self.stretch_button = self._mode_button("↔", "stretch")
        self.reconnect_button = self._action_button("↻", "reconnect")
        self.mode_group.addButton(self.fit_button)
        self.mode_group.addButton(self.fill_button)
        self.mode_group.addButton(self.stretch_button)
        toolbar_layout.addWidget(self.fit_button)
        toolbar_layout.addWidget(self.fill_button)
        toolbar_layout.addWidget(self.stretch_button)
        toolbar_layout.addWidget(self.reconnect_button)
        self.fit_button.clicked.connect(
            lambda _checked=False: self.set_display_mode("fit")
        )
        self.fill_button.clicked.connect(
            lambda _checked=False: self.set_display_mode("fill")
        )
        self.stretch_button.clicked.connect(
            lambda _checked=False: self.set_display_mode("stretch")
        )
        self.reconnect_button.clicked.connect(self.reconnect_requested.emit)

        self.recording_toolbar = QFrame(self)
        self.recording_toolbar.setObjectName("VideoModeToolbar")
        recording_layout = QHBoxLayout(self.recording_toolbar)
        recording_layout.setContentsMargins(4, 4, 4, 4)
        recording_layout.setSpacing(3)
        self.recording_button = self._action_button("●", "recording")
        self.recording_button.setProperty("recordingAction", "true")
        self.recording_button.setCheckable(True)
        self.recording_button.clicked.connect(self._request_recording_toggle)
        recording_layout.addWidget(self.recording_button)
        self._recording_enabled: bool | None = None

        if not controls:
            self.mode_toolbar.hide()
            self.recording_toolbar.hide()

        self.set_theme(theme)
        self.set_language(language)
        self.set_stats(VideoStreamStats())
        self._sync_mode_buttons()

    @property
    def display_mode(self) -> str:
        return self._display_mode

    def _mode_button(self, symbol: str, mode: str) -> QToolButton:
        button = QToolButton(self.mode_toolbar)
        button.setObjectName("VideoModeButton")
        button.setText(symbol)
        button.setProperty("viewMode", mode)
        button.setCheckable(True)
        button.setFixedSize(28, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _action_button(self, symbol: str, action: str) -> QToolButton:
        button = QToolButton(self.mode_toolbar)
        button.setObjectName("VideoModeButton")
        button.setText(symbol)
        button.setProperty("viewAction", action)
        button.setFixedSize(28, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_sources(
        self,
        sources: tuple[VideoSourceConfig, ...],
        active_identifier: str,
    ) -> None:
        del sources, active_identifier
        self._position_overlays()

    def host_controls_in(self, parent: QWidget) -> None:
        """Move the primary controls to the shared video-wall overlay.

        A tile must not own global actions such as recording or panel display
        mode: otherwise those controls move with the first tile in a split
        layout.  The panel is responsible for their fixed bottom positions.
        """
        if not self._controls or self._controls_hosted_externally:
            return
        self._controls_hosted_externally = True
        self.mode_toolbar.setParent(parent)
        self.recording_toolbar.setParent(parent)
        self._position_overlays()

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        stylesheet = f"""
            #VideoStatsOverlay {{
                background: {theme.surface};
                color: {theme.text};
                border: 1px solid {theme.border_strong};
                border-radius: 4px;
                padding: 6px 9px;
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 10px;
                font-weight: 700;
            }}
            #VideoSourceOverlay {{
                background: {theme.surface};
                color: {theme.text};
                border: 1px solid {theme.border_strong};
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 10px;
                font-weight: 750;
            }}
            #VideoModeToolbar {{
                background: {theme.surface};
                border: 1px solid {theme.border_strong};
                border-radius: 4px;
            }}
            QToolButton#VideoModeButton {{
                background: {theme.surface_alt};
                color: {theme.muted_text};
                border: 1px solid {theme.border};
                border-radius: 3px;
                padding: 0;
                font-size: 18px;
                font-weight: 700;
            }}
            QToolButton#VideoModeButton:hover {{
                background: {theme.surface_hover};
                color: {theme.text};
                border-color: {theme.border_strong};
            }}
            QToolButton#VideoModeButton:checked {{
                background: {theme.accent_dim};
                color: {theme.text};
                border-color: {theme.accent};
            }}
            QToolButton#VideoModeButton[recordingAction="true"]:checked {{
                background: #6b2525;
                border-color: #e05a5a;
            }}
            """
        self.setStyleSheet(stylesheet)
        # Once hosted by the video wall, these widgets no longer inherit this
        # viewport stylesheet.  Keep their appearance identical in both modes.
        self.mode_toolbar.setStyleSheet(stylesheet)
        self.recording_toolbar.setStyleSheet(stylesheet)

    def set_language(self, language: str) -> None:
        self._language = language
        self.fit_button.setToolTip(t("video.view.fit", language))
        self.fit_button.setAccessibleName(t("video.view.fit", language))
        self.fill_button.setToolTip(t("video.view.fill", language))
        self.fill_button.setAccessibleName(t("video.view.fill", language))
        self.stretch_button.setToolTip(t("video.view.stretch", language))
        self.stretch_button.setAccessibleName(t("video.view.stretch", language))
        self.reconnect_button.setToolTip(t("video.reconnect", language))
        self.reconnect_button.setAccessibleName(t("video.reconnect", language))
        self._sync_recording_button()
        self._render_stats()

    def set_display_mode(self, mode: str) -> None:
        normalized = mode.strip().lower()
        if normalized not in {"fit", "fill", "stretch"}:
            raise ValueError(f"Desteklenmeyen video görünüm modu: {mode}")
        changed = normalized != self._display_mode
        self._display_mode = normalized
        self._sync_mode_buttons()
        self.update()
        if changed:
            self.view_mode_changed.emit(normalized)

    def _sync_mode_buttons(self) -> None:
        self.fit_button.setChecked(self._display_mode == "fit")
        self.fill_button.setChecked(self._display_mode == "fill")
        self.stretch_button.setChecked(self._display_mode == "stretch")

    def set_reconnect_busy(self, busy: bool) -> None:
        self.reconnect_button.setEnabled(not busy)

    def set_recording_busy(self, busy: bool) -> None:
        self.recording_button.setEnabled(not busy)

    def set_recording_enabled(self, enabled: bool | None) -> None:
        self._recording_enabled = enabled
        self._sync_recording_button()

    def _request_recording_toggle(self) -> None:
        target = not bool(self._recording_enabled)
        self.recording_button.setChecked(bool(self._recording_enabled))
        self.recording_requested.emit(target)

    def _sync_recording_button(self) -> None:
        enabled = self._recording_enabled is True
        self.recording_button.setChecked(enabled)
        key = "video.recording.stop" if enabled else "video.recording.start"
        text = t(key, self._language)
        self.recording_button.setToolTip(text)
        self.recording_button.setAccessibleName(text)

    def set_stats(self, stats: VideoStreamStats) -> None:
        self._current_stats = stats
        self._render_stats()

    def _render_stats(self) -> None:
        stats = self._current_stats
        offline = not stats.connected
        self.stats_label.setProperty("offline", offline)
        if offline:
            key = (
                "video.stats.compact_waiting"
                if self._compact_overlays
                else "video.stats.waiting"
            )
            text = t(key, self._language).upper()
        elif self._compact_overlays:
            text = f"{stats.fps:.1f} FPS"
        else:
            bitrate = (
                f"{stats.bitrate_kbps / 1000.0:.1f} Mbps"
                if stats.bitrate_kbps >= 1000.0
                else f"{stats.bitrate_kbps:.0f} kbps"
            )
            text = f"{stats.width}×{stats.height}  ·  {stats.fps:.1f} FPS  ·  {bitrate}"
        self.stats_label.setText(text)
        if stats.connected:
            self.stats_label.setToolTip(
                f"{stats.width}×{stats.height} · {stats.fps:.1f} FPS · "
                f"{stats.bitrate_kbps:.0f} kbps"
            )
        else:
            self.stats_label.setToolTip(text)
        self.stats_label.adjustSize()
        self._position_overlays()

    def set_frame(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self._status = "LIVE"
        self.update()

    def clear_frame(self) -> None:
        self._pixmap = QPixmap()
        self._status = "NO SIGNAL"
        self.update()

    def set_source_name(self, name: str) -> None:
        self._source_name = name
        self._render_source_name()

    def set_compact_overlays(self, compact: bool) -> None:
        if self._compact_overlays == bool(compact):
            return
        self._compact_overlays = bool(compact)
        self._render_source_name()
        self._render_stats()

    def _render_source_name(self) -> None:
        available = max(
            70, int(self.width() * (0.48 if self._compact_overlays else 0.6))
        )
        text = self.fontMetrics().elidedText(
            self._source_name,
            Qt.TextElideMode.ElideRight,
            available,
        )
        self.source_label.setText(text)
        self.source_label.setToolTip(self._source_name)
        self.source_label.adjustSize()
        self._position_overlays()

    def frame_geometry(self) -> QRect:
        if self._pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRect()
        if self._display_mode == "stretch":
            return self.rect()
        aspect_mode = (
            Qt.AspectRatioMode.KeepAspectRatio
            if self._display_mode == "fit"
            else Qt.AspectRatioMode.KeepAspectRatioByExpanding
        )
        target_size = self._pixmap.size().scaled(self.size(), aspect_mode)
        return QRect(
            (self.width() - target_size.width()) // 2,
            (self.height() - target_size.height()) // 2,
            target_size.width(),
            target_size.height(),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_source_name()
        self._position_overlays()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self.source_label
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.source_menu_requested.emit(
                self.source_label.mapToGlobal(event.position().toPoint())
            )
            return True
        return super().eventFilter(watched, event)

    def _position_overlays(self) -> None:
        margin = 12
        self.stats_label.adjustSize()
        stats_y = margin
        self.source_label.adjustSize()
        if (
            self.source_label.width() + self.stats_label.width() + margin * 3
            > self.width()
        ):
            stats_y = margin + self.source_label.height() + 5
        self.stats_label.move(
            max(margin, self.width() - self.stats_label.width() - margin),
            stats_y,
        )
        self.stats_label.raise_()
        self.source_label.move(margin, margin)
        self.source_label.raise_()
        if not self._controls or self._controls_hosted_externally:
            return
        self.recording_toolbar.adjustSize()
        self.mode_toolbar.adjustSize()
        mode_x = max(margin, self.width() - self.mode_toolbar.width() - margin)
        mode_y = max(margin, self.height() - self.mode_toolbar.height() - margin)

        # The recording action has a stable place: lower left.  View controls
        # stay lower right and move one row up only on exceptionally narrow
        # tiles, rather than moving the primary action away from its place.
        recording_x = margin
        recording_y = max(
            margin,
            self.height() - self.recording_toolbar.height() - margin,
        )
        recording_rect = QRect(
            recording_x,
            recording_y,
            self.recording_toolbar.width(),
            self.recording_toolbar.height(),
        )
        mode_rect = QRect(
            mode_x,
            mode_y,
            self.mode_toolbar.width(),
            self.mode_toolbar.height(),
        )
        if recording_rect.intersects(mode_rect):
            mode_y = max(margin, recording_y - self.mode_toolbar.height() - 6)

        self.recording_toolbar.move(recording_x, recording_y)
        self.recording_toolbar.raise_()
        self.mode_toolbar.move(mode_x, mode_y)
        self.mode_toolbar.raise_()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._pixmap.isNull():
            painter.end()
            return

        geometry = self.frame_geometry()
        painter.drawPixmap(geometry, self._pixmap, self._pixmap.rect())
        painter.end()


class VideoPanel(PanelFrame):
    reconnect_requested = pyqtSignal()
    recording_requested = pyqtSignal(bool)
    source_selected = pyqtSignal(str)
    view_configuration_changed = pyqtSignal(int, str)

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        theme: ThemePalette,
        language: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, subtitle, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._theme = theme
        self._language = language
        self._split_count = 1
        self._layout_mode = "grid"
        self._sources: tuple[VideoSourceConfig, ...] = ()
        self._display_identifiers: list[str] = []
        self._active_identifier = ""
        self._frames: dict[str, QImage] = {}
        self._stats: dict[str, VideoStreamStats] = {}

        self.controls_toolbar = QFrame(self)
        self.controls_toolbar.setObjectName("VideoPanelToolbar")
        controls_layout = QHBoxLayout(self.controls_toolbar)
        controls_layout.setContentsMargins(4, 3, 4, 3)
        controls_layout.setSpacing(3)
        self.split_group = QButtonGroup(self)
        self.split_group.setExclusive(True)
        self.split_buttons: dict[int, QToolButton] = {}
        split_icons = {1: "▣", 2: "◫", 3: "▤", 4: "▦"}
        for count in (1, 2, 3, 4):
            button = QToolButton(self.controls_toolbar)
            button.setText(split_icons[count])
            button.setCheckable(True)
            button.setChecked(count == 1)
            button.setProperty("panelTool", "true")
            button.setFixedSize(26, 24)
            button.clicked.connect(
                lambda _checked=False, value=count: self.set_split_count(value)
            )
            self.split_group.addButton(button)
            self.split_buttons[count] = button
            controls_layout.addWidget(button)
        controls_layout.addSpacing(5)
        self.list_button = QToolButton(self.controls_toolbar)
        self.list_button.setText("☰")
        self.list_button.setCheckable(True)
        self.list_button.setProperty("panelTool", "true")
        self.list_button.setFixedSize(26, 24)
        self.grid_button = QToolButton(self.controls_toolbar)
        self.grid_button.setText("▦")
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        self.grid_button.setProperty("panelTool", "true")
        self.grid_button.setFixedSize(26, 24)
        layout_group = QButtonGroup(self)
        layout_group.setExclusive(True)
        layout_group.addButton(self.list_button)
        layout_group.addButton(self.grid_button)
        self.list_button.clicked.connect(
            lambda _checked=False: self.set_layout_mode("list")
        )
        self.grid_button.clicked.connect(
            lambda _checked=False: self.set_layout_mode("grid")
        )
        controls_layout.addWidget(self.list_button)
        controls_layout.addWidget(self.grid_button)
        controls_layout.addStretch(1)
        self.wall = QWidget(self)
        self.wall_layout = QGridLayout(self.wall)
        self.wall_layout.setContentsMargins(0, 0, 0, 0)
        self.wall_layout.setSpacing(4)
        self.controls_toolbar.setParent(self.wall)
        self.wall.installEventFilter(self)
        self.viewport = VideoViewport(theme, language, self.wall)
        self.viewport.host_controls_in(self.wall)
        self._viewports = [self.viewport]
        for index in range(3):
            self._viewports.append(
                VideoViewport(theme, language, self.wall, controls=False)
            )
        for index, viewport in enumerate(self._viewports):
            viewport.clicked.connect(
                lambda source_index=index: self._focus_viewport(source_index)
            )
            viewport.source_menu_requested.connect(
                lambda global_position, source_index=index: self._show_source_menu(
                    source_index,
                    global_position,
                )
            )
        self.viewport.reconnect_requested.connect(self.reconnect_requested.emit)
        self.viewport.recording_requested.connect(self.recording_requested.emit)
        self.viewport.view_mode_changed.connect(self._propagate_display_mode)
        self.body_layout.addWidget(self.wall, 1)
        self._apply_wall_layout()
        self.set_theme(theme)
        self.set_language(language)

    def set_frame(self, image: QImage) -> None:
        self.viewport.set_frame(image)

    def set_source_frame(self, identifier: str, image: QImage) -> None:
        self._frames[identifier] = image
        viewport = self._viewport_for_source(identifier)
        if viewport is not None:
            viewport.set_frame(image)

    def set_stats(self, stats: VideoStreamStats) -> None:
        self.viewport.set_stats(stats)

    def set_source_stats(self, item) -> None:
        self._stats[item.source_id] = item.stats
        viewport = self._viewport_for_source(item.source_id)
        if viewport is not None:
            viewport.set_stats(item.stats)

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        for viewport in self._viewports:
            viewport.set_theme(theme)
        self.setStyleSheet(
            self.styleSheet()
            + f"""
            #VideoPanelToolbar {{
                background: {theme.surface_alt};
                border: 1px solid {theme.border};
                border-radius: 4px;
            }}
            QToolButton[panelTool="true"] {{
                background: {theme.surface}; color: {theme.muted_text};
                border: 1px solid {theme.border}; border-radius: 3px;
                font-size: 12px; font-weight: 750;
            }}
            QToolButton[panelTool="true"]:checked {{
                background: {theme.accent_dim}; color: {theme.text};
                border-color: {theme.accent};
            }}
            """
        )

    def set_language(self, language: str) -> None:
        self._language = language
        for viewport in self._viewports:
            viewport.set_language(language)
        for count, button in self.split_buttons.items():
            button.setToolTip(t("video.wall.count", language).format(count=count))
        self.list_button.setToolTip(t("video.wall.list", language))
        self.grid_button.setToolTip(t("video.wall.grid", language))

    def set_reconnect_busy(self, busy: bool) -> None:
        self.viewport.set_reconnect_busy(busy)

    def set_recording_busy(self, busy: bool) -> None:
        self.viewport.set_recording_busy(busy)

    def set_recording_enabled(self, enabled: bool | None) -> None:
        self.viewport.set_recording_enabled(enabled)

    def set_sources(
        self, sources: tuple[VideoSourceConfig, ...], active_identifier: str
    ) -> None:
        previous_active = self._active_identifier
        self._sources = sources
        self._active_identifier = active_identifier
        self._sync_display_order()
        # An external source change (for example from the connection list)
        # focuses the chosen source.  A click inside the wall has already
        # arranged the order via _swap_source_into_focus, so preserve that
        # exact swap when settings are written back.
        if active_identifier != previous_active:
            self._swap_source_into_focus(active_identifier)
        self.viewport.set_sources(sources, active_identifier)
        if self._split_count > len(sources):
            self._split_count = max(
                (count for count in self.split_buttons if count <= len(sources)),
                default=1,
            )
            self.split_buttons[self._split_count].setChecked(True)
        self._sync_control_visibility()
        self._apply_wall_layout()
        self._refresh_viewports()

    def set_split_count(self, count: int) -> None:
        if count not in {1, 2, 3, 4}:
            raise ValueError(f"Desteklenmeyen video bölüm sayısı: {count}")
        self._split_count = count
        self.split_buttons[count].setChecked(True)
        self._sync_control_visibility()
        self._apply_wall_layout()
        self.view_configuration_changed.emit(count, self._layout_mode)

    def set_layout_mode(self, mode: str) -> None:
        if mode not in {"list", "grid"}:
            raise ValueError(f"Desteklenmeyen video yerleşimi: {mode}")
        self._layout_mode = mode
        self.list_button.setChecked(mode == "list")
        self.grid_button.setChecked(mode == "grid")
        self._apply_wall_layout()
        self.view_configuration_changed.emit(self._split_count, mode)

    def _select_source(self, identifier: str) -> None:
        self._swap_source_into_focus(identifier)
        self._active_identifier = identifier
        self.viewport.set_sources(self._sources, identifier)
        self._refresh_viewports()
        self.source_selected.emit(identifier)

    def _focus_viewport(self, index: int) -> None:
        ordered = self._ordered_sources()
        if index >= len(ordered):
            return
        identifier = ordered[index].identifier
        if identifier != self._active_identifier:
            self._select_source(identifier)

    def _show_source_menu(self, viewport_index: int, global_position) -> None:
        """Offer each configured input for one visible wall tile.

        The display order owns tile assignment.  Replacing a tile therefore
        swaps identifiers rather than copying one into a second viewport.
        """
        ordered = self._ordered_sources()
        if viewport_index >= len(ordered) or len(ordered) < 2:
            return
        menu = QMenu(self)
        current_identifier = ordered[viewport_index].identifier
        for source in ordered:
            action = menu.addAction(source.name)
            action.setData(source.identifier)
            action.setCheckable(True)
            action.setChecked(source.identifier == current_identifier)
        selected = menu.exec(global_position)
        if selected is None:
            return
        identifier = str(selected.data())
        self._select_source_for_viewport(viewport_index, identifier)

    def _select_source_for_viewport(self, viewport_index: int, identifier: str) -> None:
        """Put ``identifier`` in one tile, exchanging it with its old tile."""
        if viewport_index < 0 or viewport_index >= len(self._display_identifiers):
            return
        if identifier not in self._display_identifiers:
            return
        current_identifier = self._display_identifiers[viewport_index]
        if identifier == current_identifier:
            return
        source_index = self._display_identifiers.index(identifier)
        (
            self._display_identifiers[viewport_index],
            self._display_identifiers[source_index],
        ) = (
            self._display_identifiers[source_index],
            self._display_identifiers[viewport_index],
        )
        # Selecting an input also makes it the capture/share focus.  Keep the
        # tile arrangement intact while the settings update returns.
        self._active_identifier = identifier
        self.viewport.set_sources(self._sources, identifier)
        self._refresh_viewports()
        self.source_selected.emit(identifier)

    def _ordered_sources(self) -> tuple[VideoSourceConfig, ...]:
        by_identifier = {source.identifier: source for source in self._sources}
        return tuple(
            by_identifier[identifier]
            for identifier in self._display_identifiers
            if identifier in by_identifier
        )

    def _sync_display_order(self) -> None:
        valid = {source.identifier for source in self._sources}
        retained = [
            identifier
            for identifier in self._display_identifiers
            if identifier in valid
        ]
        retained_set = set(retained)
        retained.extend(
            source.identifier
            for source in self._sources
            if source.identifier not in retained_set
        )
        self._display_identifiers = retained

    def _swap_source_into_focus(self, identifier: str) -> None:
        if identifier not in self._display_identifiers:
            return
        index = self._display_identifiers.index(identifier)
        if index:
            self._display_identifiers[0], self._display_identifiers[index] = (
                self._display_identifiers[index],
                self._display_identifiers[0],
            )

    def _apply_wall_layout(self) -> None:
        for viewport in self._viewports:
            self.wall_layout.removeWidget(viewport)
            viewport.hide()
        visible_count = min(self._split_count, max(1, len(self._sources)))
        for index, viewport in enumerate(self._viewports[:visible_count]):
            if self._layout_mode == "list":
                row, column = index, 0
            elif visible_count == 2:
                row, column = 0, index
            elif visible_count == 3 and index == 2:
                self.wall_layout.addWidget(viewport, 1, 0, 1, 2)
                viewport.show()
                continue
            else:
                row, column = divmod(index, 2)
            self.wall_layout.addWidget(viewport, row, column)
            viewport.show()
        self._refresh_viewports()
        self._position_floating_toolbar()

    def _refresh_viewports(self) -> None:
        ordered = self._ordered_sources()
        for index, viewport in enumerate(self._viewports):
            if index >= len(ordered):
                viewport.set_source_name("")
                viewport.set_stats(VideoStreamStats())
                viewport.clear_frame()
                continue
            source = ordered[index]
            # Tile labels identify the configured connection itself.  The
            # order is already conveyed by the wall layout and selector, so
            # avoid prepending unstable 1/2/3 numbering to camera names.
            viewport.set_source_name(source.name)
            stats = self._stats.get(source.identifier, VideoStreamStats())
            viewport.set_stats(stats)
            frame = self._frames.get(source.identifier)
            if frame is not None:
                viewport.set_frame(frame)
            else:
                viewport.clear_frame()

    def _viewport_for_source(self, identifier: str) -> VideoViewport | None:
        ordered = self._ordered_sources()
        for index, source in enumerate(ordered[: len(self._viewports)]):
            if source.identifier == identifier and self._viewports[index].isVisible():
                return self._viewports[index]
        return None

    def _propagate_display_mode(self, mode: str) -> None:
        for viewport in self._viewports[1:]:
            viewport.set_display_mode(mode)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.wall and event.type() == QEvent.Type.Resize:
            self._position_floating_toolbar()
        return super().eventFilter(watched, event)

    def _sync_control_visibility(self) -> None:
        source_count = len(self._sources)
        self.split_buttons[1].setVisible(source_count > 1)
        self.split_buttons[2].setVisible(source_count >= 2)
        self.split_buttons[3].setVisible(source_count >= 3)
        self.split_buttons[4].setVisible(source_count >= 4)
        multi = self._split_count > 1
        self.list_button.setVisible(multi)
        self.grid_button.setVisible(multi)
        for viewport in self._viewports:
            viewport.set_compact_overlays(multi)
        self.controls_toolbar.setVisible(source_count > 1)
        self._position_floating_toolbar()

    def _position_floating_toolbar(self) -> None:
        self.controls_toolbar.adjustSize()
        x = max(8, (self.wall.width() - self.controls_toolbar.width()) // 2)
        y = max(8, self.wall.height() - self.controls_toolbar.height() - 8)

        # Panel controls deliberately share the bottom strip with the record
        # and view controls.  Their fixed positions make the camera UI easy
        # to scan and prevent controls from jumping while the layout changes.
        self.controls_toolbar.move(x, y)
        self.controls_toolbar.raise_()

        # These are panel-level actions, not tile actions.  Their coordinates
        # are deliberately relative to the wall, so splitting the feed never
        # drags them into the left/top viewport.
        recording = self.viewport.recording_toolbar
        mode = self.viewport.mode_toolbar
        recording.adjustSize()
        mode.adjustSize()
        recording.move(
            8,
            max(8, self.wall.height() - recording.height() - 8),
        )
        mode.move(
            max(8, self.wall.width() - mode.width() - 8),
            max(8, self.wall.height() - mode.height() - 8),
        )
        recording.raise_()
        mode.raise_()
