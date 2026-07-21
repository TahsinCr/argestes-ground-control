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
    #WindowTitleLabel {{
        color: {theme.text};
        font-size: 12px;
        font-weight: 650;
        letter-spacing: 0.2px;
    }}
    #WindowControls {{
        background: transparent;
    }}
    #ArchiveList {{
        padding: 5px;
    }}
    #ArchiveList::item {{
        padding: 0;
        margin: 3px 0;
        border-radius: 4px;
    }}
    #ArchiveList::item:selected {{
        background: {theme.accent_dim};
        color: {theme.text};
    }}
    #ArchiveRow {{
        background: transparent;
    }}
    #ArchiveDeleteButton {{
        min-width: 28px;
        max-width: 28px;
        min-height: 24px;
        max-height: 24px;
        padding: 0;
        border-radius: 5px;
        font-size: 16px;
        font-weight: 700;
        background: transparent;
        border: 1px solid transparent;
        color: {theme.muted_text};
    }}
    #ArchiveDeleteButton:hover {{
        background: rgba(244, 71, 71, 0.14);
        border-color: {theme.danger};
        color: {theme.danger};
    }}
    QLabel[role="archive-name"] {{
        color: {theme.text};
        font-size: 12px;
        font-weight: 650;
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
        border-bottom: 1px solid rgba(255,255,255,0.035);
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
        border: 1px solid rgba(255,255,255,0.055);
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
    #TelemetrySectionTitle {{
        margin-top: 2px;
        color: {theme.muted_text};
        font-size: 9px;
        font-weight: 750;
        letter-spacing: 0.6px;
    }}
    #TelemetrySection {{
        background: {theme.background_alt};
        border: 1px solid rgba(255,255,255,0.045);
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
        border-bottom: 1px solid rgba(255,255,255,0.03);
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
        border-bottom: 1px solid rgba(255,255,255,0.04);
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
