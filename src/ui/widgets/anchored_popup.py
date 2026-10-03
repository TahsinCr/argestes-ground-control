"""Screen-bounded positioning shared by extension-owned status popups."""

from PyQt6.QtCore import QPoint, Qt


def open_above_anchor(popup, anchor):
    popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
    screen = anchor.screen().availableGeometry()
    popup.setMinimumSize(
        min(popup.minimumWidth(), screen.width()),
        min(popup.minimumHeight(), screen.height()),
    )
    popup.resize(
        min(popup.sizeHint().width(), screen.width()),
        min(popup.sizeHint().height(), screen.height()),
    )
    origin = anchor.mapToGlobal(QPoint(0, 0))
    popup.move(
        max(screen.left(), min(origin.x(), screen.right() - popup.width() + 1)),
        max(
            screen.top(),
            min(origin.y() - popup.height() - 6, screen.bottom() - popup.height() + 1),
        ),
    )
    popup.show()
    popup.raise_()
