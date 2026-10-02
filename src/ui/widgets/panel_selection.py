"""Non-destructive panel selection intercepts clicks above panel content."""

from PyQt6.QtCore import Qt, QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QApplication,
)


class _SelectionSurface(QPushButton):
    def __init__(self, key, panel, owner):
        super().__init__(panel)
        self.setObjectName("PanelSelectionSurface")
        self.setCheckable(True)
        label = QLabel(panel.windowTitle(), self)
        label.setObjectName("PanelSelectionLabel")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName(panel.windowTitle())
        self.setGeometry(panel.rect())
        self.clicked.connect(lambda checked: owner.select(key, checked))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        panel.installEventFilter(self)
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False


class PanelSelection(QWidget):
    completed = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, workspace, language):
        super().__init__(workspace)
        self.workspace = workspace
        self.selected = set()
        self.surfaces = []
        self.setObjectName("PanelSelectionActions")
        root = QVBoxLayout(self)
        root.addWidget(
            QLabel("Select panels" if language == "en" else "Panelleri seçin", self)
        )
        row = QHBoxLayout()
        self.cancel = QPushButton("Cancel" if language == "en" else "İptal", self)
        self.add = QPushButton("Add" if language == "en" else "Ekle", self)
        self.add.setEnabled(False)
        row.addWidget(self.cancel)
        row.addWidget(self.add)
        root.addLayout(row)
        self.cancel.clicked.connect(self.cancelled.emit)
        self.add.clicked.connect(lambda: self.completed.emit(sorted(self.selected)))
        for key, panel in workspace.panels.items():
            if panel.isVisible():
                self.surfaces.append(_SelectionSurface(key, panel, self))
        workspace.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self.reposition()
        self.show()
        self.raise_()

    def select(self, key, selected):
        if selected:
            self.selected.add(key)
        else:
            self.selected.discard(key)
        self.add.setEnabled(bool(self.selected))

    def reposition(self):
        self.adjustSize()
        self.move(
            max(0, self.workspace.width() - self.width() - 16),
            max(0, self.workspace.height() - self.height() - 16),
        )

    def eventFilter(self, watched, event):
        if watched is self.workspace and event.type() == QEvent.Type.Resize:
            self.reposition()
        elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return True
        return False

    def dispose(self):
        self.workspace.removeEventFilter(self)
        QApplication.instance().removeEventFilter(self)
        for surface in self.surfaces:
            surface.parentWidget().removeEventFilter(surface)
            surface.hide()
            surface.deleteLater()
        self.hide()
        self.deleteLater()
