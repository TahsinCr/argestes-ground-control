"""View selection above, per-view panel categories below."""

from PyQt6.QtCore import QEvent, Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QPainter, QPen, QPalette
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QToolButton,
    QFrame,
    QMenu,
    QScrollArea,
    QButtonGroup,
)
from src.ui.widgets.workspace_icons import workspace_icon


class _WorkspaceButton(QToolButton):
    """A small group-state marker, without another outlined button style."""

    def paintEvent(self, event):
        super().paintEvent(event)
        state = self.property("groupState")
        if state not in ("open", "partial") or not self.isEnabled():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(QPalette.ColorRole.Highlight)
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(color if state == "open" else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(self.width() - 11, self.height() - 11, 4, 4)


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
        self._active_view_id = None
        self._can_remove_view = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._ensure_active_view)
        self._top, self._top_layout = self._section(layout)
        self.add_button = QToolButton(self)
        self.add_button.setIcon(workspace_icon("plus"))
        self.add_button.setIconSize(QSize(18, 18))
        self.add_button.setObjectName("ActivityBarButton")
        self.add_button.setFixedSize(44, 36)
        self.add_button.clicked.connect(self._create_menu)
        layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        line = QFrame(self)
        line.setObjectName("WorkspaceSectionDivider")
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
        body.setObjectName("WorkspaceBarSection")
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
        menu.deleteLater()

    def _context_menu(self, identifier, category, button):
        english = self.language == "en"
        menu = QMenu(self)
        menu.addAction(
            "Edit" if english else "Düzenle",
            lambda: self.edit_requested.emit(identifier, category),
        )
        delete_action = menu.addAction(
            "Delete" if english else "Sil",
            lambda: self.remove_requested.emit(identifier, category),
        )
        delete_action.setEnabled(category or self._can_remove_view)
        menu.exec(button.mapToGlobal(button.rect().bottomRight()))
        menu.deleteLater()

    def _reconcile(self, items, buttons, layout, *, category):
        ids = {item["id"] for item in items}
        for key in list(buttons):
            if key not in ids:
                button = buttons.pop(key)
                layout.removeWidget(button)
                self._view_group.removeButton(button)
                button.hide()
                button.deleteLater()
        for index, item in enumerate(items):
            identifier = item["id"]
            button = buttons.get(identifier)
            if button is None:
                button = _WorkspaceButton(self)
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
            icon = item.get("icon", "layout")
            if button.property("workspaceIcon") != icon:
                button.setIcon(workspace_icon(icon))
                button.setProperty("workspaceIcon", icon)
            button.setToolTip(item["name"])
            button.setAccessibleName(item["name"])
            if layout.indexOf(button) != index:
                layout.insertWidget(index, button)
        (self._bottom if category else self._top).setFixedHeight(
            min(240, max(1, len(items) * (38 if category else 44)))
        )

    def refresh(self, controller):
        self._can_remove_view = len(controller.views) > 1
        self._reconcile(
            controller.views, self.view_buttons, self._top_layout, category=False
        )
        groups = controller.active["categories"] if controller.active else []
        self._reconcile(
            groups, self.category_buttons, self._bottom_layout, category=True
        )
        for view in controller.views:
            key = view["id"]
            button = self.view_buttons[key]
            button.setChecked(key == controller.active_id)
            kind = "View" if self.language == "en" else "Görünüm"
            button.setToolTip(f"{view['name']} · {kind}")
        if self._active_view_id != controller.active_id:
            self._active_view_id = controller.active_id
            self._scroll_timer.start(0)
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
                button.update()
            text = (
                f"{opened}/{len(docks)} panels open"
                if self.language == "en"
                else f"{opened}/{len(docks)} panel açık"
            )
            button.setToolTip(f"{group['name']}\n{text}")

    def set_language(self, language):
        self.language = language
        self.add_button.setToolTip(
            "Add view or category" if language == "en" else "Görünüm veya kategori ekle"
        )

    def _ensure_active_view(self):
        button = self.view_buttons.get(self._active_view_id)
        if button is not None:
            self._top.ensureWidgetVisible(button)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "add_button"):
            self.add_button.setIcon(workspace_icon("plus"))
            for button in (
                *self.view_buttons.values(),
                *self.category_buttons.values(),
            ):
                name = button.property("workspaceIcon")
                if name:
                    button.setIcon(workspace_icon(name))
