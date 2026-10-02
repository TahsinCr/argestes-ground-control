"""Shared searchable name/icon picker for views and panel categories."""

from PyQt6.QtCore import QEvent, Qt, QSize
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QStyle,
    QLabel,
)
from src.ui.widgets.workspace_icons import ICON_NAMES, workspace_icon


def extension_icon(widget):
    from PyQt6.QtGui import QPixmap, QPainter, QPainterPath, QPen, QIcon, QPalette

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(widget.palette().color(QPalette.ColorRole.ButtonText), 1.8))
    path = QPainterPath()
    path.moveTo(6, 8)
    path.lineTo(12, 8)
    path.cubicTo(10, 1, 20, 1, 18, 8)
    path.lineTo(24, 8)
    path.lineTo(24, 14)
    path.cubicTo(31, 12, 31, 22, 24, 20)
    path.lineTo(24, 26)
    path.lineTo(18, 26)
    path.cubicTo(20, 19, 10, 19, 12, 26)
    path.lineTo(6, 26)
    path.lineTo(6, 20)
    path.cubicTo(13, 22, 13, 12, 6, 14)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class WorkspaceItemDialog(QDialog):
    def __init__(self, language, parent=None, *, category=False, item=None):
        super().__init__(parent)
        english = language == "en"
        self.setWindowTitle(
            ("Category" if category else "View")
            if english
            else ("Kategori" if category else "Görünüm")
        )
        self.resize(520, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        heading = QLabel(self.windowTitle(), self)
        heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(heading)
        root.addWidget(QLabel("Name" if english else "İsim", self))
        self.name_input = QLineEdit(self)
        self.name_input.setMaxLength(80)
        self.name_input.setPlaceholderText("Name" if english else "İsim")
        root.addWidget(self.name_input)
        search = QLineEdit(self)
        search.setPlaceholderText("Search icons" if english else "İkon ara")
        root.addWidget(search)
        self.icons = QListWidget(self)
        self.icons.setObjectName("WorkspaceIconPicker")
        self.icons.setViewMode(QListWidget.ViewMode.IconMode)
        self.icons.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.icons.setIconSize(QSize(24, 24))
        self.icons.setGridSize(QSize(104, 70))
        self.icons.setWordWrap(True)
        for identifier, names in ICON_NAMES.items():
            entry = QListWidgetItem(
                workspace_icon(identifier), names[0 if english else 1]
            )
            entry.setData(Qt.ItemDataRole.UserRole, identifier)
            entry.setToolTip(entry.text())
            self.icons.addItem(entry)
        for value in QStyle.StandardPixmap:
            if value.name == "SP_CustomBase":
                continue
            icon = workspace_icon(value.name)
            if icon.isNull():
                continue
            label = value.name.removeprefix("SP_").removesuffix("Icon")
            entry = QListWidgetItem(icon, label)
            entry.setData(Qt.ItemDataRole.UserRole, value.name)
            entry.setToolTip(label)
            self.icons.addItem(entry)
        root.addWidget(self.icons, 1)
        search.textChanged.connect(self.filter_icons)
        if category and item is None:
            root.addWidget(
                QLabel(
                    "Next, click the visible panels to group them."
                    if english
                    else "Sonraki adımda gruplamak istediğiniz açık panellere tıklayın."
                )
            )
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            ("Save" if item else "Create")
            if english
            else ("Kaydet" if item else "Oluştur")
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "Cancel" if english else "İptal"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.name_input.textChanged.connect(
            lambda text: self.buttons.button(
                QDialogButtonBox.StandardButton.Ok
            ).setEnabled(bool(text.strip()))
        )
        self.name_input.setText(item["name"] if item else "")
        selected = item.get("icon") if item else "layers" if category else "layout"
        self.icons.setCurrentRow(
            next(
                (
                    i
                    for i in range(self.icons.count())
                    if self.icons.item(i).data(Qt.ItemDataRole.UserRole) == selected
                ),
                0,
            )
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.name_input.text().strip())
        )

    def filter_icons(self, text):
        for i in range(self.icons.count()):
            entry = self.icons.item(i)
            entry.setHidden(text.casefold() not in entry.text().casefold())

    def values(self):
        return self.name_input.text().strip(), self.icons.currentItem().data(
            Qt.ItemDataRole.UserRole
        )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "icons"):
            for index in range(self.icons.count()):
                item = self.icons.item(index)
                item.setIcon(workspace_icon(item.data(Qt.ItemDataRole.UserRole)))
