from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from src.models.video import VideoStreamStats
from src.ui.config.i18n import t
from src.ui.themes.style import ThemePalette
from src.ui.widgets.common import PanelFrame


class VideoViewport(QWidget):
    view_mode_changed = pyqtSignal(str)
    reconnect_requested = pyqtSignal()

    def __init__(
        self,
        theme: ThemePalette,
        language: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._status = "NO SIGNAL"
        self._theme = theme
        self._language = language
        self._display_mode = "fit"
        self.setMinimumHeight(320)
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

        self.mode_toolbar = QFrame(self)
        self.mode_toolbar.setObjectName("VideoModeToolbar")
        toolbar_layout = QHBoxLayout(self.mode_toolbar)
        toolbar_layout.setContentsMargins(6, 6, 6, 6)
        toolbar_layout.setSpacing(4)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.fit_button = self._mode_button("▣", "fit")
        self.fill_button = self._mode_button("⛶", "fill")
        self.reconnect_button = self._action_button("↻", "reconnect")
        self.mode_group.addButton(self.fit_button)
        self.mode_group.addButton(self.fill_button)
        toolbar_layout.addWidget(self.fit_button)
        toolbar_layout.addWidget(self.fill_button)
        toolbar_layout.addWidget(self.reconnect_button)
        self.fit_button.clicked.connect(
            lambda _checked=False: self.set_display_mode("fit")
        )
        self.fill_button.clicked.connect(
            lambda _checked=False: self.set_display_mode("fill")
        )
        self.reconnect_button.clicked.connect(self.reconnect_requested.emit)

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
        button.setFixedSize(34, 34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _action_button(self, symbol: str, action: str) -> QToolButton:
        button = QToolButton(self.mode_toolbar)
        button.setObjectName("VideoModeButton")
        button.setText(symbol)
        button.setProperty("viewAction", action)
        button.setFixedSize(34, 34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
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
            """
        )

    def set_language(self, language: str) -> None:
        self._language = language
        self.fit_button.setToolTip(t("video.view.fit", language))
        self.fit_button.setAccessibleName(t("video.view.fit", language))
        self.fill_button.setToolTip(t("video.view.fill", language))
        self.fill_button.setAccessibleName(t("video.view.fill", language))
        self.reconnect_button.setToolTip(t("video.reconnect", language))
        self.reconnect_button.setAccessibleName(t("video.reconnect", language))
        if self.stats_label.property("offline"):
            self.stats_label.setText(t("video.stats.waiting", language).upper())
            self._position_overlays()

    def set_display_mode(self, mode: str) -> None:
        normalized = mode.strip().lower()
        if normalized not in {"fit", "fill"}:
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

    def set_reconnect_busy(self, busy: bool) -> None:
        self.reconnect_button.setEnabled(not busy)

    def set_stats(self, stats: VideoStreamStats) -> None:
        offline = not stats.connected
        self.stats_label.setProperty("offline", offline)
        if offline:
            text = t("video.stats.waiting", self._language).upper()
        else:
            bitrate = (
                f"{stats.bitrate_kbps / 1000.0:.1f} Mbps"
                if stats.bitrate_kbps >= 1000.0
                else f"{stats.bitrate_kbps:.0f} kbps"
            )
            text = (
                f"{stats.width}×{stats.height}  ·  "
                f"{stats.fps:.1f} FPS  ·  {bitrate}"
            )
        self.stats_label.setText(text)
        self.stats_label.adjustSize()
        self._position_overlays()

    def set_frame(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self._status = "LIVE"
        self.update()

    def frame_geometry(self) -> QRect:
        if self._pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRect()
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
        self._position_overlays()

    def _position_overlays(self) -> None:
        margin = 12
        self.stats_label.adjustSize()
        self.stats_label.move(
            max(margin, self.width() - self.stats_label.width() - margin),
            margin,
        )
        self.stats_label.raise_()
        self.mode_toolbar.adjustSize()
        self.mode_toolbar.move(
            max(margin, self.width() - self.mode_toolbar.width() - margin),
            max(margin, self.height() - self.mode_toolbar.height() - margin),
        )
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
        self.viewport = VideoViewport(theme, language, self)
        self.viewport.reconnect_requested.connect(self.reconnect_requested.emit)
        self.body_layout.addWidget(self.viewport, 1)

    def set_frame(self, image: QImage) -> None:
        self.viewport.set_frame(image)

    def set_stats(self, stats: VideoStreamStats) -> None:
        self.viewport.set_stats(stats)

    def set_theme(self, theme: ThemePalette) -> None:
        self.viewport.set_theme(theme)

    def set_language(self, language: str) -> None:
        self.viewport.set_language(language)

    def set_reconnect_busy(self, busy: bool) -> None:
        self.viewport.set_reconnect_busy(busy)
