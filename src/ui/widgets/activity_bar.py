"""View selection above, per-view panel categories below."""

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QToolButton,
    QFrame,
    QMenu,
    QScrollArea,
    QButtonGroup,
)
from src.ui.widgets.workspace_dialog import standard_icon


class ActivityBar(QWidget):
    view_requested = pyqtSignal(str)
    category_requested = pyqtSignal(str)
    create_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str, bool)
    remove_requested = pyqtSignal(str, bool)

    def __init__(self, language, parent=None):
        super().__init__(parent)
        self.language = language
        self.setObjectName("ActivityBar")
        self.setFixedWidth(48)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        self.view_buttons = {}
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self.category_buttons = {}
        self._top, self._top_layout = self._section(layout)
        self.add_button = QToolButton(self)
        self.add_button.setText("+")
        self.add_button.setObjectName("ActivityBarButton")
        self.add_button.setFixedSize(44, 36)
        self.add_button.clicked.connect(self._create_menu)
        layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)
        self._bottom, self._bottom_layout = self._section(layout)
        self.set_language(language)

    def _section(self, layout):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(240)
        body = QWidget(scroll)
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        return scroll, column

    def _create_menu(self):
        english = self.language == "en"
        menu = QMenu(self)
        menu.addAction(
            "New view" if english else "Görünüm ekle",
            lambda: self.create_requested.emit("view"),
        )
        menu.addAction(
            "New category" if english else "Kategori ekle",
            lambda: self.create_requested.emit("category"),
        )
        menu.exec(self.add_button.mapToGlobal(self.add_button.rect().bottomRight()))

    def _context_menu(self, identifier, category, button):
        english = self.language == "en"
        menu = QMenu(self)
        menu.addAction(
            "Edit" if english else "Düzenle",
            lambda: self.edit_requested.emit(identifier, category),
        )
        menu.addAction(
            "Delete" if english else "Sil",
            lambda: self.remove_requested.emit(identifier, category),
        )
        menu.exec(button.mapToGlobal(button.rect().bottomRight()))

    def _reconcile(self, items, buttons, layout, *, category):
        ids = {item["id"] for item in items}
        for key in list(buttons):
            if key not in ids:
                button = buttons.pop(key)
                layout.removeWidget(button)
                button.hide()
                button.deleteLater()
        for index, item in enumerate(items):
            identifier = item["id"]
            button = buttons.get(identifier)
            if button is None:
                button = QToolButton(self)
                button.setObjectName("ActivityBarButton")
                button.setFixedSize(44, 42 if not category else 36)
                button.setIconSize(
                    QSize(22 if not category else 18, 22 if not category else 18)
                )
                button.setCheckable(not category)
                if not category:
                    self._view_group.addButton(button)
                button.setProperty("category", category)
                button.clicked.connect(
                    lambda _=False, key=identifier: (
                        self.category_requested if category else self.view_requested
                    ).emit(key)
                )
                button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                button.customContextMenuRequested.connect(
                    lambda _, key=identifier, b=button: self._context_menu(
                        key, category, b
                    )
                )
                buttons[identifier] = button
            button.setIcon(
                standard_icon(item.get("icon", "SP_FileDialogDetailedView"), button)
            )
            button.setToolTip(item["name"])
            button.setAccessibleName(item["name"])
            layout.insertWidget(index, button)
        (self._bottom if category else self._top).setFixedHeight(
            min(240, max(1, len(items) * (38 if category else 44)))
        )

    def refresh(self, controller):
        self._reconcile(
            controller.views, self.view_buttons, self._top_layout, category=False
        )
        groups = controller.active["categories"] if controller.active else []
        self._reconcile(
            groups, self.category_buttons, self._bottom_layout, category=True
        )
        for key, button in self.view_buttons.items():
            button.setChecked(key == controller.active_id)
        for group in groups:
            docks = [
                controller.workspace.panels[key]
                for key in group["panels"]
                if key in controller.workspace.panels
            ]
            opened = sum(not dock.isHidden() for dock in docks)
            button = self.category_buttons[group["id"]]
            button.setEnabled(bool(docks))
            status = (
                "open"
                if docks and opened == len(docks)
                else "partial"
                if opened
                else "closed"
            )
            if button.property("groupState") != status:
                button.setProperty("groupState", status)
                button.style().unpolish(button)
                button.style().polish(button)

    def set_language(self, language):
        self.language = language
        self.add_button.setToolTip(
            "Add view or category" if language == "en" else "Görünüm veya kategori ekle"
        )
