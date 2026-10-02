from __future__ import annotations

from src.ui.themes.palette import ThemePalette


def build_stylesheet(theme: ThemePalette) -> str:
    return f"""
    QWidget {{
        background: {theme.background};
        color: {theme.text};
        selection-background-color: {theme.accent};
        selection-color: #ffffff;
        font-family: "Noto Sans";
        font-size: 12px;
    }}
    QLabel {{
        background: transparent;
    }}
    QMainWindow {{
        background: {theme.background};
    }}
    QMainWindow#PanelWorkspace {{
        background: {theme.background_alt};
    }}
    #ActivityBar QToolButton[category="true"] {{
        margin: 2px 4px;
        border-radius: 6px;
        background: transparent;
        border: none;
    }}
    #ActivityBar QToolButton[category="true"]:hover {{
        background: {theme.surface_hover};
    }}
    #WorkspaceBarSection {{
        background: transparent;
    }}
    QListWidget#WorkspaceIconPicker::item {{
        border: 1px solid transparent;
        border-radius: 6px;
        margin: 2px;
        padding: 3px;
    }}
    QListWidget#WorkspaceIconPicker::item:hover {{
        background: {theme.surface_hover};
    }}
    QListWidget#WorkspaceIconPicker::item:selected {{
        background: {theme.surface_hover};
        color: {theme.text};
        border: 1px solid {theme.accent};
    }}
    #WorkspaceSectionDivider {{
        color: {theme.border_subtle};
        background: {theme.border_subtle};
        border: none;
        max-height: 1px;
        margin: 6px 10px;
    }}
    #PanelSelectionSurface {{
        background: rgba(100, 100, 100, 35);
        border: 2px dashed {theme.border_strong};
        border-radius: 4px;
        color: {theme.text};
        font-size: 15px;
    }}
    #PanelSelectionSurface:checked {{
        background: rgba(100, 100, 100, 75);
        border: 2px solid {theme.accent};
    }}
    #PanelSelectionLabel {{
        background: {theme.surface_alt};
        color: {theme.text};
        border: 1px solid {theme.border};
        border-radius: 6px;
        padding: 6px 12px;
    }}
    #PanelSelectionActions {{
        background: {theme.surface};
        border: 1px solid {theme.border_strong};
        border-radius: 6px;
    }}
    QMainWindow#PanelWorkspace QDockWidget {{
        color: {theme.text};
        font-size: 11px;
        font-weight: 650;
        border: 1px solid {theme.border};
    }}
    QMainWindow#PanelWorkspace QDockWidget::title {{
        background: {theme.surface_alt};
        border-bottom: 1px solid {theme.border};
        padding: 6px 10px;
        text-align: left;
    }}
    QMainWindow#PanelWorkspace QDockWidget::close-button,
    QMainWindow#PanelWorkspace QDockWidget::float-button {{
        background: {theme.surface_hover};
        border-radius: 3px;
        padding: 2px;
    }}
    QMainWindow#PanelWorkspace QDockWidget::close-button:hover,
    QMainWindow#PanelWorkspace QDockWidget::float-button:hover {{
        background: {theme.accent_dim};
    }}
    QMainWindow#PanelWorkspace QTabBar::tab {{
        background: {theme.surface};
        color: {theme.muted_text};
        border: 1px solid {theme.border};
        padding: 6px 12px;
        min-width: 88px;
    }}
    QMainWindow#PanelWorkspace QTabBar::tab:selected {{
        background: {theme.surface_alt};
        color: {theme.text};
        border-top: 2px solid {theme.accent};
    }}
    QDialog#FramelessDialog {{
        background: {theme.surface};
        border: 1px solid {theme.border_strong};
    }}
    QSplitter::handle {{
        background: {theme.border};
    }}
    QSplitter::handle:horizontal {{
        width: 1px;
    }}
    QSplitter::handle:vertical {{
        height: 1px;
    }}
    #TopBar {{
        background: {theme.surface};
        border: none;
        border-bottom: 1px solid {theme.border};
        border-radius: 0;
    }}
    #AppTitleBar {{
        background: {theme.surface_alt};
        border-bottom: 1px solid {theme.border};
    }}
    #ActivityBar {{
        background: {theme.surface_alt};
        border-right: 1px solid {theme.border};
    }}
    QToolButton#ActivityBarButton {{
        background: transparent;
        color: {theme.muted_text};
        border: none;
        border-left: 2px solid transparent;
        border-radius: 0;
        padding: 0;
    }}
    QToolButton#ActivityBarButton:hover {{
        background: {theme.surface_hover};
        color: {theme.text};
    }}
    QToolButton#ActivityBarButton:checked {{
        background: {theme.surface_hover};
        color: {theme.text};
        border-left: 2px solid {theme.accent};
    }}
    #WindowTitleLabel {{
        color: {theme.text};
        font-size: 12px;
        font-weight: 650;
        letter-spacing: 0.2px;
    }}
    #WindowControls {{
        background: transparent;
    }}
    QMenuBar#MainMenuBar {{
        background: transparent;
        border: none;
        spacing: 2px;
        padding: 0;
    }}
    QMenuBar#MainMenuBar::item {{
        background: transparent;
        color: {theme.muted_text};
        padding: 5px 8px;
        border-radius: 3px;
    }}
    QMenuBar#MainMenuBar::item:selected,
    QMenuBar#MainMenuBar::item:pressed {{
        background: {theme.surface_hover};
        color: {theme.text};
    }}
    QMenu {{
        background: {theme.surface};
        color: {theme.text};
        border: 1px solid {theme.border_strong};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 5px 28px 5px 8px;
        border-radius: 3px;
    }}
    QMenu::item:selected {{
        background: {theme.surface_hover};
    }}
    QPushButton[windowControl="true"] {{
        background: transparent;
        border: none;
        border-radius: 0;
        min-height: 0;
        min-width: 0;
        padding: 0;
        color: {theme.text};
        font-size: 13px;
        font-weight: 700;
    }}
    QPushButton[windowControl="true"]:hover {{
        background: {theme.surface_hover};
        border: none;
        color: {theme.text};
    }}
    QPushButton[closeControl="true"]:hover {{
        background: {theme.danger};
        color: #ffffff;
    }}
    #BottomStatusBar {{
        background: {theme.surface_alt};
        border-top: 1px solid {theme.border};
    }}
    #SettingsIconButton {{
        min-height: 26px;
        min-width: 38px;
        padding: 0;
        font-size: 16px;
        font-weight: 800;
        border-radius: 5px;
    }}
    #BrandTitle {{
        font-size: 16px;
        font-weight: 700;
        letter-spacing: 0.3px;
    }}
    #BrandSubtitle {{
        color: {theme.muted_text};
        font-size: 10px;
    }}
    #PanelFrame {{
        background: {theme.surface};
        border: none;
        border-right: 1px solid {theme.border};
        border-bottom: 1px solid {theme.border};
        border-radius: 0;
    }}
    #PanelHeader {{
        background: {theme.surface};
        border: none;
        border-bottom: 1px solid {theme.border_subtle};
    }}
    #PanelBody {{
        background: {theme.surface};
        border: none;
    }}
    #PanelTitle {{
        font-size: 11px;
        font-weight: 700;
        color: {theme.text};
        letter-spacing: 0.5px;
    }}
    #PanelSubtitle {{
        color: {theme.muted_text};
        font-size: 10px;
    }}
    #MetricCard {{
        background: {theme.surface};
        border: 1px solid {theme.border_subtle};
        border-radius: 4px;
    }}
    #MetricTitle {{
        color: {theme.muted_text};
        font-size: 10px;
        text-transform: uppercase;
    }}
    #MetricValue {{
        font-size: 15px;
        font-weight: 700;
        background: transparent;
    }}
    #MetricDetail {{
        color: {theme.muted_text};
        font-size: 10px;
    }}
    QProgressBar#ActuatorTestProgress {{
        background: {theme.background_alt};
        border: 1px solid {theme.border};
        border-radius: 3px;
        padding: 0;
    }}
    QProgressBar#ActuatorTestProgress::chunk {{
        background: {theme.accent};
        border-radius: 2px;
    }}
    QProgressBar#ActuatorTestProgress[state="success"]::chunk {{
        background: {theme.success};
    }}
    QProgressBar#ActuatorTestProgress[state="danger"]::chunk {{
        background: {theme.danger};
    }}
    QProgressBar#ActuatorTestProgress[state="warning"]::chunk {{
        background: {theme.warning};
    }}
    #TelemetrySectionTitle {{
        margin-top: 2px;
        color: {theme.muted_text};
        font-size: 9px;
        font-weight: 750;
        letter-spacing: 0.6px;
    }}
    #TelemetrySection {{
        background: {theme.background_alt};
        border: 1px solid {theme.border_subtle};
        border-radius: 4px;
    }}
    QLabel[role="readout-label"] {{
        color: {theme.muted_text};
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.4px;
    }}
    QLabel[role="readout-value"] {{
        color: {theme.text};
        font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
        font-size: 10px;
        font-weight: 600;
    }}
    QTabWidget#CommandTabs::pane {{
        background: {theme.background_alt};
        border: 1px solid {theme.border};
        border-radius: 5px;
        top: 0;
    }}
    QTabWidget#CommandTabs QTabBar {{
        background: transparent;
    }}
    QTabWidget#CommandTabs QTabBar::tab {{
        min-height: 29px;
        padding: 0 12px;
        margin: 0;
        background: {theme.surface};
        border: 1px solid {theme.border};
        border-bottom: none;
        color: {theme.muted_text};
        font-size: 10px;
        font-weight: 700;
    }}
    QTabWidget#CommandTabs QTabBar::tab:first {{
        border-top-left-radius: 5px;
    }}
    QTabWidget#CommandTabs QTabBar::tab:last {{
        border-top-right-radius: 5px;
        border-left: none;
    }}
    QTabWidget#CommandTabs QTabBar::tab:hover {{
        color: {theme.text};
        background: {theme.surface_hover};
    }}
    QTabWidget#CommandTabs QTabBar::tab:selected {{
        color: {theme.text};
        background: {theme.background_alt};
        border-top: 2px solid {theme.accent};
    }}
    QScrollArea#CommandTabScroll,
    QWidget#CommandTabPage {{
        background: transparent;
        border: none;
    }}
    QScrollArea#CommandTabScroll > QWidget > QWidget,
    QWidget#CommandTabContent {{
        background: {theme.background_alt};
        border: none;
    }}
    QFrame#QuickCommandCard {{
        background: {theme.surface};
        border: 1px solid {theme.border_subtle};
        border-radius: 5px;
    }}
    QLabel#CommandStatus {{
        min-height: 20px;
        padding: 4px 7px;
        background: {theme.surface};
        border: 1px solid {theme.border};
        border-radius: 4px;
    }}
    QPushButton {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border_strong};
        border-radius: 4px;
        min-height: 30px;
        padding: 0 10px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {theme.surface_hover};
        border-color: {theme.accent};
    }}
    QPushButton:checked {{
        background: {theme.accent_dim};
        border-color: {theme.accent};
        color: #ffffff;
    }}
    QPushButton:disabled {{
        background: {theme.surface};
        border-color: {theme.border};
        color: {theme.muted_text};
    }}
    QPushButton[role="primary"] {{
        background: {theme.accent};
        border-color: {theme.accent};
        color: #ffffff;
    }}
    QPushButton[role="danger"] {{
        background: rgba(244, 71, 71, 0.12);
        border-color: {theme.danger};
        color: {theme.danger};
    }}
    QPushButton[role="danger"]:checked {{
        background: rgba(244, 71, 71, 0.16);
        border-color: {theme.danger};
        color: {theme.danger};
    }}
    QPushButton[role="success"] {{
        background: rgba(106, 153, 85, 0.14);
        border-color: {theme.success};
        color: {theme.success};
    }}
    QPushButton[role="success"]:checked {{
        background: rgba(106, 153, 85, 0.18);
        border-color: {theme.success};
        color: {theme.success};
    }}
    QPushButton[compact="true"] {{
        min-height: 26px;
        padding: 0 8px;
        font-size: 11px;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QTreeWidget, QListWidget, QGroupBox {{
        background: {theme.background_alt};
        border: 1px solid {theme.border_strong};
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {theme.accent};
    }}
    QComboBox {{
        min-height: 26px;
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        min-height: 26px;
    }}
    QComboBox QAbstractItemView, QTableWidget, QListWidget {{
        selection-background-color: {theme.accent};
        selection-color: #ffffff;
    }}
    #LogLevelFilter {{
        min-height: 28px;
        max-height: 28px;
        padding: 0 28px 0 9px;
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 6px;
        color: {theme.text};
        font-size: 10px;
        font-weight: 800;
    }}
    QLineEdit#LogSearchInput {{
        background: {theme.surface_alt};
        border-color: {theme.border};
    }}
    QLineEdit#LogSearchInput:hover {{
        background: {theme.surface_hover};
        border-color: {theme.border_strong};
    }}
    #LogLevelFilter:hover {{
        background: {theme.surface_hover};
        border-color: {theme.border_strong};
    }}
    #LogLevelFilter:focus,
    #LogLevelFilter:on {{
        background: {theme.surface_hover};
        border-color: {theme.accent};
    }}
    #LogLevelFilter::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border: none;
        border-left: 1px solid {theme.border};
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
    }}
    #LogLevelFilter::down-arrow {{
        width: 8px;
        height: 8px;
    }}
    #LogLevelFilter QAbstractItemView {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border_strong};
        border-radius: 5px;
        padding: 4px;
        color: {theme.text};
        outline: 0;
        selection-background-color: {theme.accent_dim};
        selection-color: {theme.text};
    }}
    #LogLevelFilter QAbstractItemView::item {{
        min-height: 25px;
        padding: 3px 8px;
        border: none;
        border-radius: 3px;
    }}
    #LogLevelFilter QAbstractItemView::item:hover {{
        background: {theme.surface_hover};
    }}
    #LogLevelFilter QAbstractItemView::item:selected {{
        background: {theme.accent_dim};
        color: {theme.text};
    }}
    QTableWidget {{
        background: {theme.background_alt};
        gridline-color: transparent;
        alternate-background-color: {theme.surface};
        border-left: none;
        border-right: none;
        border-bottom: none;
    }}
    QTableWidget::item, QListWidget::item {{
        background: transparent;
        color: {theme.text};
        padding: 6px 8px;
        border: 0;
    }}
    QAbstractItemView::item:selected,
    QAbstractItemView::item:selected:active,
    QAbstractItemView::item:selected:!active,
    QTableWidget::item:selected,
    QListWidget::item:selected,
    QListWidget::item:selected:active,
    QListWidget::item:selected:!active {{
        background: {theme.accent};
        color: #ffffff;
        border-color: {theme.accent};
    }}
    QListWidget::item {{
        min-height: 24px;
        border-bottom: 1px solid {theme.border_subtle};
    }}
    QListWidget::item:hover {{
        background: {theme.surface_hover};
    }}
    QListWidget::item:alternate {{
        background: transparent;
    }}
    #LogTable {{
        font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
        font-size: 11px;
        background: {theme.background_alt};
    }}
    #LogTable::item {{
        padding: 4px 6px;
        border-bottom: 1px solid {theme.border_subtle};
    }}
    QHeaderView::section {{
        background: {theme.background_alt};
        color: {theme.muted_text};
        border: none;
        border-bottom: 1px solid {theme.border};
        padding: 5px;
        font-size: 10px;
        font-weight: 700;
    }}
    QGroupBox {{
        margin-top: 6px;
        padding-top: 10px;
        font-weight: 700;
        color: {theme.muted_text};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QScrollBar:vertical {{
        width: 10px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        background: {theme.surface_hover};
        border-radius: 4px;
        min-height: 28px;
    }}
    QToolTip {{
        background: {theme.surface_alt};
        color: {theme.text};
        border: 1px solid {theme.border_strong};
        padding: 4px 6px;
    }}
    QLabel[role="section-label"] {{
        color: {theme.muted_text};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.4px;
        text-transform: uppercase;
    }}
    QLabel[role="note"] {{
        color: {theme.muted_text};
        font-size: 10px;
    }}
    QLabel[state="success"], QWidget[state="success"] {{
        color: {theme.success};
    }}
    QLabel[state="warning"], QWidget[state="warning"] {{
        color: {theme.warning};
    }}
    QLabel[state="danger"], QWidget[state="danger"] {{
        color: {theme.danger};
    }}
    """
