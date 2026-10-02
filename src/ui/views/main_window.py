from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.models.settings import SettingsModel
from src.ui.i18n import t
from src.ui.assets import application_icon
from src.ui.themes.style import apply_theme
from src.ui.views.settings_dialog import SettingsDialog
from src.ui.widgets.logs import EventLogPanel
from src.ui.widgets.activity_bar import ActivityBar
from src.ui.widgets.title_bar import AppTitleBar
from src.ui.widgets.panel_workspace import PanelWorkspace


@dataclass(frozen=True)
class _VisualDockPreference:
    area: Qt.DockWidgetArea
    width: int
    height: int
    floating: bool
    tab_target: str | None = None
    anchor: str | None = None
    orientation: Qt.Orientation = Qt.Orientation.Horizontal
    after_anchor: bool = True


class MainWindow(QMainWindow):
    shutdown_requested = pyqtSignal()
    _WORKSPACE_AREAS = Qt.DockWidgetArea.AllDockWidgetAreas

    def __init__(
        self, *, log_service, settings_service, base_dir, theme, extension_host
    ):
        super().__init__()
        self.log_service = log_service
        self.settings_service = settings_service
        self.base_dir = base_dir
        self.extension_host = extension_host
        self.theme = theme
        self._settings = settings_service.current
        self.language = self._settings.language
        self._layout_restored = False
        self._workspace_disconnected = False
        self._visual_regions = {}
        self.setWindowTitle(t("app.title", self.language))
        self.setWindowIcon(application_icon(base_dir))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(1600, 940)
        self.setMinimumSize(1152, 648)
        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)
        self.title_bar = AppTitleBar(self, self.theme, central)
        self.title_bar.set_title(self.windowTitle())
        self.title_bar.enable_main_menu(self.language)
        layout.addWidget(self.title_bar)
        content = QWidget(central)
        row = QHBoxLayout(content)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.activity_bar = ActivityBar(self.language, content)
        row.addWidget(self.activity_bar)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal, content)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(4)
        self._sidebar_last_width = 320
        self._sidebar_explicitly_visible = False
        self.sidebar = QStackedWidget(self.content_splitter)
        self.sidebar.setObjectName("ExtensionSidebar")
        self.sidebar.setMinimumWidth(280)
        self._extension_sidebar_widgets = {}
        self.workspace = PanelWorkspace(
            self.base_dir / "data" / "workspace-layout.json",
            self.content_splitter,
            language=self.language,
        )
        self.content_splitter.addWidget(self.sidebar)
        self.content_splitter.addWidget(self.workspace)
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)
        self.sidebar.hide()
        row.addWidget(self.content_splitter, 1)
        layout.addWidget(content, 1)
        self._build_workspace()
        self.activity_bar.sidebar_view_requested.connect(self._show_sidebar_view)
        self.activity_bar.flight_sidebar_toggled.connect(self._toggle_flight_panels)
        self.activity_bar.panel_visibility_requested.connect(
            self._set_workspace_panel_visibility
        )
        self.activity_bar.bottom_panel_toggled.connect(self._on_bottom_panel_toggled)
        self.activity_bar.bottom_panel_side_by_side_requested.connect(
            self._open_bottom_panel_beside
        )
        self._build_view_menu()
        self.status_bar = QWidget(central)
        self.status_bar.setObjectName("BottomStatusBar")
        footer = QHBoxLayout(self.status_bar)
        footer.setContentsMargins(8, 3, 8, 3)
        footer.setSpacing(6)
        self._status_connections_anchor = QWidget(self.status_bar)
        self._status_connections_anchor.setFixedWidth(0)
        footer.addWidget(self._status_connections_anchor)
        self._extension_status_widgets = []
        self._extension_status_by_id = {}
        footer.addStretch(1)
        self._status_metrics_anchor = QWidget(self.status_bar)
        self._status_metrics_anchor.setFixedWidth(0)
        footer.addWidget(self._status_metrics_anchor)
        self.extensions_button = QPushButton("▦", self.status_bar)
        self.extensions_button.setObjectName("SettingsIconButton")
        self.extensions_button.setProperty("compact", "true")
        self.extensions_button.setAccessibleName(
            t("activity.extensions", self.language)
        )
        self.extensions_button.setFixedWidth(42)
        self.extensions_button.setToolTip(t("activity.extensions", self.language))
        self.extensions_button.clicked.connect(self.open_extensions)
        footer.addWidget(self.extensions_button)
        self.settings_button = QPushButton("⚙", self.status_bar)
        self.settings_button.setObjectName("SettingsIconButton")
        self.settings_button.setProperty("compact", "true")
        self.settings_button.setFixedWidth(42)
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.settings_button.clicked.connect(self.open_settings)
        footer.addWidget(self.settings_button)
        for contribution in self.extension_host.status_widgets():
            self._add_status_contribution(contribution)
        layout.addWidget(self.status_bar)
        from src.ui.widgets.extension_panel import ExtensionDialog

        self.extension_dialog = ExtensionDialog(
            self.extension_host,
            self.language,
            self,
            apply_change=self._apply_extension_change,
            reload_extension=self._reload_extension,
        )
        self.extension_panel = self.extension_dialog.browser
        for contribution in self.extension_host.sidebar_views():
            widget = contribution.factory(self.sidebar)
            self.sidebar.addWidget(widget)
            self._extension_sidebar_widgets[contribution.identifier] = widget
            self.activity_bar.add_extension_view(
                contribution.identifier, contribution.title
            )

    def _show_sidebar_view(self, view):
        widget = self._extension_sidebar_widgets.get(view)
        if widget is None:
            self._set_extension_sidebar_visible(False)
            return
        self.sidebar.setCurrentWidget(widget)
        self._set_extension_sidebar_visible(True)

    def _set_extension_sidebar_visible(self, visible: bool) -> None:
        if visible == self._sidebar_explicitly_visible:
            return
        if not visible:
            width = self.sidebar.width()
            if width >= self.sidebar.minimumWidth():
                self._sidebar_last_width = width
            self._sidebar_explicitly_visible = False
            self.sidebar.hide()
            return
        self._sidebar_explicitly_visible = True
        self.sidebar.show()
        width = max(280, self._sidebar_last_width)
        QTimer.singleShot(
            0,
            lambda remembered=width: self.content_splitter.setSizes(
                [remembered, max(1, self.content_splitter.width() - remembered)]
            ),
        )

    def _toggle_flight_panels(self, visible: bool) -> None:
        if visible:
            self._set_extension_sidebar_visible(False)
        for dock in self._flight_docks:
            dock.setVisible(visible)

    def _register_workspace_panel_button(self, key: str, title: str, dock) -> None:
        button = self.activity_bar.add_panel_button(key, title)
        button.setChecked(not dock.isHidden())
        dock.visibilityChanged.connect(
            lambda _visible, panel_key=key, panel=dock: (
                self.activity_bar.set_panel_checked(panel_key, not panel.isHidden())
            )
        )

    def _set_workspace_panel_visibility(self, key: str, visible: bool) -> None:
        dock = self.workspace.panels.get(key)
        if dock is None:
            return
        dock.setVisible(visible)
        if visible:
            dock.raise_()

    def _apply_extension_change(self, identifier: str, enabled: bool) -> None:
        was_active = self.extension_host.is_active(identifier)
        self.extension_host.set_enabled(identifier, enabled)
        try:
            self._sync_extension_ui(identifier)
        except Exception:
            self._remove_extension_ui(identifier)
            self.extension_host.set_enabled(identifier, was_active)
            self._sync_extension_ui(identifier)
            raise

    def _reload_extension(self, identifier: str) -> None:
        self._remove_extension_ui(identifier)
        self.extension_host.reload(identifier)
        self._sync_extension_ui(identifier)

    def _sync_extension_ui(self, identifier):
        self._remove_extension_ui(identifier)
        if not self.extension_host.is_active(identifier):
            return
        prefix = identifier + "."
        for contribution in self.extension_host.visual_views():
            if contribution.identifier.startswith(prefix):
                self._add_visual_contribution(contribution)
        for contribution in self.extension_host.status_widgets():
            if contribution.identifier.startswith(prefix):
                self._add_status_contribution(contribution)
        for contribution in self.extension_host.sidebar_views():
            if contribution.identifier.startswith(prefix):
                widget = contribution.factory(self.sidebar)
                self.sidebar.addWidget(widget)
                self._extension_sidebar_widgets[contribution.identifier] = widget
                self.activity_bar.add_extension_view(
                    contribution.identifier, contribution.title
                )
        self._refresh_panel_menu()

    def _add_status_contribution(self, contribution) -> None:
        widget = contribution.factory(self.status_bar, self.theme, self.language)
        if not isinstance(widget, QWidget):
            raise TypeError("Extension status must return QWidget")
        footer = self.status_bar.layout()
        index = footer.indexOf(
            self._status_metrics_anchor
            if contribution.placement == "metrics"
            else self._status_connections_anchor
        )
        footer.insertWidget(index, widget)
        self._extension_status_widgets.append(widget)
        self._extension_status_by_id[contribution.identifier] = widget

    def _remove_extension_ui(self, identifier: str) -> None:
        prefix = identifier + "."
        for key, widget in tuple(self._extension_status_by_id.items()):
            if key.startswith(prefix):
                shutdown = getattr(widget, "prepare_shutdown", None)
                if callable(shutdown):
                    shutdown()
                widget.parentWidget().layout().removeWidget(widget)
                widget.hide()
                widget.deleteLater()
                self._extension_status_widgets.remove(widget)
                del self._extension_status_by_id[key]
        for key, widget in tuple(self._extension_visual_by_id.items()):
            if key.startswith(prefix):
                shutdown = getattr(widget, "prepare_shutdown", None)
                if callable(shutdown):
                    shutdown()
                dock = self.workspace.panels.get(key)
                if dock is not None:
                    self._visual_dock_preferences[key] = (
                        self._capture_visual_dock_preference(key, dock)
                    )
                self.workspace.remove_panel(key)
                self.activity_bar.remove_panel_button(key)
                if widget is self.primary_view:
                    self.primary_view = self.primary_dock = None
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                self._extension_visual_widgets.remove(widget)
                del self._extension_visual_by_id[key]
                self._visual_regions.pop(key, None)
                self._flight_docks = tuple(
                    d for d in self._flight_docks if d is not dock
                )
                if self.parameters_dock is dock:
                    self.parameters_dock = None
        self._refresh_panel_menu()
        for key, widget in tuple(self._extension_sidebar_widgets.items()):
            if key.startswith(prefix):
                if self.sidebar.currentWidget() is widget:
                    self._set_extension_sidebar_visible(False)
                self.sidebar.removeWidget(widget)
                self.activity_bar.remove_extension_view(key)
                widget.deleteLater()
                del self._extension_sidebar_widgets[key]

    def _build_workspace(self):
        self.primary_view = self.primary_dock = None
        self._extension_visual_widgets = []
        self._extension_visual_by_id = {}
        self._visual_dock_preferences = {}
        self._flight_docks = ()
        self.log_panel = EventLogPanel("", "", "", self.workspace)
        self.log_dock = self.workspace.add_panel(
            "logs",
            "Loglar",
            self.log_panel,
            Qt.DockWidgetArea.BottomDockWidgetArea,
            allowed_areas=self._WORKSPACE_AREAS,
        )
        self.parameters_dock = None
        for contribution in sorted(
            self.extension_host.visual_views(),
            key=lambda item: (not item.primary, item.order),
        ):
            self._add_visual_contribution(contribution)

    def _add_visual_contribution(self, contribution):
        widget = contribution.factory(self.workspace, self.theme, self.language)
        if not isinstance(widget, QWidget):
            raise TypeError("Extension visual must return QWidget")
        areas = {
            "left": Qt.DockWidgetArea.LeftDockWidgetArea,
            "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
            "workspace": Qt.DockWidgetArea.RightDockWidgetArea,
        }
        key = contribution.identifier
        preference = self._visual_dock_preferences.get(key)
        dock = self.workspace.add_panel(
            key,
            contribution.title or self._visual_title(key),
            widget,
            preference.area if preference else areas[contribution.region],
            allowed_areas=self._WORKSPACE_AREAS,
            floatable=False,
            legacy_key=contribution.legacy_key,
        )
        neighbors = [
            self.workspace.panels[item]
            for item, region in self._visual_regions.items()
            if region == contribution.region and item in self.workspace.panels
        ]
        anchor = (
            neighbors[-1]
            if neighbors
            else self.log_dock
            if contribution.region == "bottom"
            else None
        )
        if preference and preference.tab_target in self.workspace.panels:
            self.workspace.place_panel(
                self.workspace.panels[preference.tab_target], dock, "tab"
            )
        elif preference and preference.anchor in self.workspace.panels:
            direction = (
                ("right" if preference.after_anchor else "left")
                if preference.orientation == Qt.Orientation.Horizontal
                else ("below" if preference.after_anchor else "above")
            )
            self.workspace.place_panel(
                self.workspace.panels[preference.anchor], dock, direction
            )
        elif anchor is not None:
            self.workspace.place_panel(
                anchor,
                dock,
                "below"
                if contribution.region == "left"
                else "tab"
                if contribution.region == "bottom"
                else "right",
            )
        self._visual_regions[key] = contribution.region
        if contribution.region == "left":
            self._flight_docks = (*self._flight_docks, dock)
        if contribution.legacy_key == "parameters":
            self.parameters_dock = dock
        else:
            self._register_workspace_panel_button(key, dock.windowTitle(), dock)
        dock.visibilityChanged.connect(lambda _: self._sync_activity_buttons())
        dock.setVisible(contribution.initial_visible)
        if contribution.primary:
            self.primary_view, self.primary_dock = widget, dock
        self._extension_visual_widgets.append(widget)
        self._extension_visual_by_id[key] = widget
        if preference:
            self._size_visual_dock(dock, preference)
        self._sync_activity_buttons()

    def open_extensions(self):
        self.extension_dialog.browser.refresh()
        self.extension_dialog.resize_for(self)
        self.extension_dialog.show()
        self.extension_dialog.raise_()
        self.extension_dialog.activateWindow()

    def _on_bottom_panel_toggled(self, panel, checked):
        selected = self.log_dock if panel == "logs" else self.parameters_dock
        other = self.parameters_dock if panel == "logs" else self.log_dock
        if selected is None:
            return
        if checked:
            if other is not None:
                other.hide()
            selected.show()
            selected.raise_()
        else:
            selected.hide()

    def _open_bottom_panel_beside(self, panel):
        selected = self.log_dock if panel == "logs" else self.parameters_dock
        other = self.parameters_dock if panel == "logs" else self.log_dock
        if selected is None:
            return
        if other is None or other.isHidden():
            self._on_bottom_panel_toggled(panel, True)
            return
        self.workspace.place_panel(other, selected, "right")
        selected.show()

    def _build_view_menu(self) -> None:
        menu = self.title_bar._menu_bar.actions()[3].menu()
        self._panel_menu = menu.addMenu("Paneller")
        self._refresh_panel_menu()
        menu.addSeparator()
        self._edit_layout_action = menu.addAction("Yerleşimi düzenle")
        self._edit_layout_action.setCheckable(True)
        self._edit_layout_action.toggled.connect(self._set_layout_edit_mode)
        self._edit_layout_action.setShortcut("Ctrl+Alt+L")
        self._focus_action = menu.addAction("Haritaya odaklan", self._focus_map)
        self._reset_action = menu.addAction(
            "Varsayılan yerleşimi geri yükle", self._reset_workspace
        )
        self._theme_menu = menu.addMenu("Tema")
        self._dark_action = self._theme_menu.addAction(
            "Koyu", lambda: self._set_theme_choice("dark")
        )
        self._light_action = self._theme_menu.addAction(
            "Açık", lambda: self._set_theme_choice("light")
        )
        self._retranslate_view_menu()

    def _refresh_panel_menu(self) -> None:
        if hasattr(self, "_panel_menu"):
            self._populate_panel_actions(self._panel_menu)

    def _populate_panel_actions(self, menu):
        menu.clear()
        for key, dock in self.workspace.panels.items():
            menu.addAction(dock.toggleViewAction())

    def _visual_title(self, identifier: str) -> str:
        name = identifier.split(".")[0]
        key = f"extension.{name}.name"
        translated = t(key, self.language)
        if translated != f"[{key}]":
            return translated
        return name.replace("_", " ").title()

    def _retranslate_view_menu(self) -> None:
        english = self.language == "en"
        self._panel_menu.setTitle("Panels" if english else "Paneller")
        self._edit_layout_action.setText(
            "Edit Layout" if english else "Yerleşimi düzenle"
        )
        self._focus_action.setText(
            "Focus Main View" if english else "Ana görünüme odaklan"
        )
        self._reset_action.setText(
            "Reset Layout" if english else "Varsayılan yerleşimi geri yükle"
        )
        self._theme_menu.setTitle("Theme" if english else "Tema")
        self._dark_action.setText("Dark" if english else "Koyu")
        self._light_action.setText("Light" if english else "Açık")
        self.workspace.language = self.language
        labels = {
            "map": "Map" if english else "Harita",
            "flight": "Flight Dynamics" if english else "Araç Dinamiği",
            "telemetry": "Vehicle Telemetry" if english else "Araç Telemetrisi",
            "commands": "Quick Commands" if english else "Hızlı Komutlar",
            "logs": "Logs" if english else "Loglar",
            "parameters": "Parameters" if english else "Parametreler",
        }
        for key, dock in self.workspace.panels.items():
            dock.setWindowTitle(labels.get(key, self._visual_title(key)))
            if key in self.activity_bar.panel_buttons:
                self.activity_bar.set_panel_title(key, dock.windowTitle())

    def _set_layout_edit_mode(self, enabled: bool) -> None:
        self.workspace.set_edit_mode(enabled)

    def _set_theme_choice(self, theme_name: str) -> None:
        if self._settings.theme == theme_name:
            return
        payload = self._settings.to_dict()
        payload["ui"]["theme"] = theme_name
        self.settings_service.save_async(SettingsModel.from_dict(payload))

    def _focus_map(self) -> None:
        if self.primary_dock is None:
            return
        self._set_extension_sidebar_visible(False)
        for dock in self.workspace.panels.values():
            dock.hide()
        if self.primary_dock is not None:
            self.primary_dock.show()
            self.primary_dock.raise_()
        self.activity_bar.sidebar_button.setChecked(False)
        self.activity_bar.extensions_button.setChecked(False)
        for key in self.activity_bar.buttons:
            self.activity_bar.set_bottom_panel_checked(key, False)

    def _reset_workspace(self):
        regions = {"left": [], "bottom": [self.log_dock], "workspace": []}
        areas = {
            "left": Qt.DockWidgetArea.LeftDockWidgetArea,
            "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
            "workspace": Qt.DockWidgetArea.RightDockWidgetArea,
        }
        for key, dock in self.workspace.panels.items():
            if key == "logs":
                self.workspace.move_panel_to_area(dock, areas["bottom"])
                dock.show()
                continue
            region = self._visual_regions.get(key, "workspace")
            self.workspace.move_panel_to_area(dock, areas[region])
            if regions[region]:
                self.workspace.place_panel(
                    regions[region][-1],
                    dock,
                    "below"
                    if region == "left"
                    else "tab"
                    if region == "bottom"
                    else "right",
                )
            regions[region].append(dock)
            dock.setVisible(region != "bottom")
        QTimer.singleShot(0, self._size_default_docks)

    def _size_default_docks(self):
        self.workspace._panel_size_restore_enabled = False
        if self._flight_docks:
            heights = {
                item.identifier: item.preferred_height
                for item in self.extension_host.visual_views()
            }
            sizes = [
                heights.get(self.workspace._panel_keys[dock], 240)
                for dock in self._flight_docks
            ]
            sizes[-1] = max(sizes[-1], self.workspace.height() - sum(sizes[:-1]))
            self.workspace.resizeDocks(
                list(self._flight_docks), sizes, Qt.Orientation.Vertical
            )
        if self.primary_dock is not None:
            self.workspace.resizeDocks(
                [self.primary_dock, self.log_dock],
                [max(240, self.workspace.height() - 266), 260],
                Qt.Orientation.Vertical,
            )
            if self._flight_docks:
                self.workspace.resizeDocks(
                    [self._flight_docks[0], self.primary_dock],
                    [320, max(400, self.workspace.width() - 326)],
                    Qt.Orientation.Horizontal,
                )
        self.workspace.layout().activate()
        self.workspace.enable_panel_size_restore(capture_current=True)

    def _size_visual_dock(
        self,
        dock,
        preference: _VisualDockPreference | None,
    ) -> None:
        if dock not in self.workspace.panels.values() or dock.isFloating():
            return
        area = self.workspace.dockWidgetArea(dock)
        orientation = (
            Qt.Orientation.Horizontal
            if area
            in (
                Qt.DockWidgetArea.LeftDockWidgetArea,
                Qt.DockWidgetArea.RightDockWidgetArea,
            )
            else Qt.Orientation.Vertical
        )
        if preference:
            size = (
                preference.width
                if orientation == Qt.Orientation.Horizontal
                else preference.height
            )
        else:
            size = (
                max(380, self.workspace.width() // 3)
                if orientation == Qt.Orientation.Horizontal
                else max(240, self.workspace.height() // 3)
            )
        self.workspace.resizeDocks([dock], [size], orientation)

    def _capture_visual_dock_preference(self, key: str, dock) -> _VisualDockPreference:
        peers = self.workspace.tabifiedDockWidgets(dock)
        tab_target = next(
            (
                panel_key
                for panel_key, panel in self.workspace.panels.items()
                if panel in peers
            ),
            None,
        )
        anchor = None
        orientation = Qt.Orientation.Horizontal
        after_anchor = True
        best_gap: int | None = None
        if tab_target is None and not dock.isFloating():
            area = self.workspace.dockWidgetArea(dock)
            source = dock.geometry()
            for other_key, other in self.workspace.panels.items():
                if (
                    other_key == key
                    or other.isFloating()
                    or not other.isVisible()
                    or self.workspace.dockWidgetArea(other) != area
                ):
                    continue
                rect = other.geometry()
                vertical_overlap = max(
                    0,
                    min(source.bottom(), rect.bottom()) - max(source.top(), rect.top()),
                )
                horizontal_overlap = max(
                    0,
                    min(source.right(), rect.right()) - max(source.left(), rect.left()),
                )
                if vertical_overlap >= min(source.height(), rect.height()) // 3:
                    is_after = source.center().x() >= rect.center().x()
                    gap = (
                        source.left() - rect.right()
                        if is_after
                        else rect.left() - source.right()
                    )
                    if gap >= -8 and (best_gap is None or gap < best_gap):
                        anchor, orientation, after_anchor, best_gap = (
                            other_key,
                            Qt.Orientation.Horizontal,
                            is_after,
                            gap,
                        )
                if horizontal_overlap >= min(source.width(), rect.width()) // 3:
                    is_after = source.center().y() >= rect.center().y()
                    gap = (
                        source.top() - rect.bottom()
                        if is_after
                        else rect.top() - source.bottom()
                    )
                    if gap >= -8 and (best_gap is None or gap < best_gap):
                        anchor, orientation, after_anchor, best_gap = (
                            other_key,
                            Qt.Orientation.Vertical,
                            is_after,
                            gap,
                        )
        return _VisualDockPreference(
            area=self.workspace.dockWidgetArea(dock),
            width=dock.width(),
            height=dock.height(),
            floating=dock.isFloating(),
            tab_target=tab_target,
            anchor=anchor,
            orientation=orientation,
            after_anchor=after_anchor,
        )

    def _restore_workspace(self) -> None:
        if self._workspace_disconnected:
            return
        if not self.workspace.restore_layout():
            self._size_default_docks()
            self.workspace.enable_panel_size_restore(capture_current=True)
        self._sync_activity_buttons()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._layout_restored:
            self._layout_restored = True
            QTimer.singleShot(0, self._restore_workspace)

    def _sync_activity_buttons(self):
        self.activity_bar.sidebar_button.setChecked(
            any(not dock.isHidden() for dock in self._flight_docks)
        )
        self.activity_bar.set_bottom_panel_checked("logs", not self.log_dock.isHidden())
        self.activity_bar.buttons["parameters"].setVisible(
            self.parameters_dock is not None
        )
        self.activity_bar.set_bottom_panel_checked(
            "parameters",
            self.parameters_dock is not None and not self.parameters_dock.isHidden(),
        )

    def _connect_signals(self):
        self.log_service.entries_emitted.connect(self.log_panel.add_entries)
        self.settings_service.settings_changed.connect(self._settings_changed)
        self.log_service.reemit_history()

    def open_settings(self):
        dialog = SettingsDialog(self._settings, self.language, self.theme, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings_service.save_async(dialog.to_settings())
        dialog.deleteLater()

    def _settings_changed(self, settings):
        previous = self._settings
        self._settings = settings
        if previous.ui == settings.ui:
            return
        self.language = settings.language
        app = QApplication.instance()
        if app is not None:
            self.theme = apply_theme(app, settings.theme)
        self.setWindowTitle(t("app.title", self.language))
        self.title_bar.set_theme(self.theme)
        self.title_bar.set_title(self.windowTitle())
        self.title_bar.set_menu_language(self.language)
        self._retranslate_view_menu()
        self.activity_bar.set_language(self.language)
        self.extension_panel.set_language(self.language)
        self.extensions_button.setToolTip(t("activity.extensions", self.language))
        self.settings_button.setToolTip(t("status.settings", self.language))
        self.extension_dialog.setWindowTitle(t("activity.extensions", self.language))
        self.extension_dialog.tabs.setTabText(
            0, "Installed" if self.language == "en" else "Yüklü"
        )
        self.log_panel.set_language(self.language)
        for widget in (
            *self._extension_visual_widgets,
            *self._extension_status_widgets,
        ):
            for method, value in (
                ("set_theme", self.theme),
                ("set_language", self.language),
            ):
                callback = getattr(widget, method, None)
                if callable(callback):
                    callback(value)

    def prepare_shutdown(self):
        if self._workspace_disconnected:
            return
        self._workspace_disconnected = True
        for widget in (
            *self._extension_visual_widgets,
            *self._extension_status_widgets,
        ):
            shutdown = getattr(widget, "prepare_shutdown", None)
            if callable(shutdown):
                shutdown()

    def closeEvent(self, event) -> None:
        self.workspace.save_layout()
        self.prepare_shutdown()
        self.shutdown_requested.emit()
        super().closeEvent(event)


__all__ = ["MainWindow"]
