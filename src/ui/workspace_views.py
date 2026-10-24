"""Per-view dock snapshots and panel groups; never owns feature services."""

from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QEvent, Qt


class WorkspaceViews(QObject):
    changed = pyqtSignal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.views: list[dict] = []
        self.active_id = ""
        self._busy = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self.save)
        workspace.installEventFilter(self)
        workspace.layout_changed.connect(self.invalidate_group_layouts)

    def invalidate_group_layouts(self):
        if not self._busy and self.active:
            for group in self.active["categories"]:
                group.pop("layout", None)

    @property
    def active(self):
        return next((view for view in self.views if view["id"] == self.active_id), None)

    def initialize(self, groups=()):
        try:
            stored = json.loads(
                self.workspace.state_path.read_text(encoding="utf-8")
            ).get("views", {})
            seen = set()
            for view in stored.get("items", []):
                if (
                    not isinstance(view, dict)
                    or not isinstance(view.get("id"), str)
                    or view["id"] in seen
                ):
                    continue
                if not isinstance(view.get("name"), str) or not isinstance(
                    view.get("layout"), dict
                ):
                    continue
                seen.add(view["id"])
                categories = []
                category_ids = set()
                for group in view.get("categories", []):
                    if (
                        not isinstance(group, dict)
                        or not isinstance(group.get("panels"), list)
                        or not isinstance(group.get("id"), str)
                        or not isinstance(group.get("name"), str)
                        or group["id"] in category_ids
                    ):
                        continue
                    category_ids.add(group["id"])
                    group["panels"] = list(
                        dict.fromkeys(
                            key for key in group["panels"] if isinstance(key, str)
                        )
                    )
                    if not isinstance(group.get("layout"), dict):
                        group.pop("layout", None)
                    if not isinstance(group.get("icon"), str):
                        group["icon"] = "grid"
                    categories.append(group)
                view["categories"] = categories
                if not isinstance(view.get("icon"), str):
                    view["icon"] = "layout"
                self.views.append(view)
            self.active_id = stored.get("active", "")
        except (OSError, ValueError, TypeError, AttributeError):
            self.views = []
        if not self.views:
            self.views = [
                {
                    "id": uuid4().hex,
                    "name": "Ana görünüm",
                    "icon": "layout",
                    "layout": self.workspace.capture_layout(),
                    "categories": list(groups),
                }
            ]
        if self.active is None:
            self.active_id = self.views[0]["id"]
        if not self._apply(self.active["layout"]):
            self.active["layout"] = self.workspace.capture_layout()
        self.changed.emit()

    def track_panel(self, dock):
        dock.visibilityChanged.connect(self._visibility_changed)
        dock.installEventFilter(self)

    def eventFilter(self, watched, event):
        if not self._busy and self.active is not None:
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self._save_timer.start()
            elif (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                # An explicit workspace interaction makes an old group snapshot
                # stale. Never undo subsequent placement edits on group reopen.
                self.invalidate_group_layouts()
        return False

    def _visibility_changed(self, *_):
        if not self._busy:
            self.changed.emit()
            self._save_timer.start()

    def _apply(self, layout):
        was_busy = self._busy
        updates_enabled = self.workspace.updatesEnabled()
        self._busy = True
        self.workspace.setUpdatesEnabled(False)
        try:
            return self.workspace.restore_layout(layout, restore_window=False)
        finally:
            self.workspace.setUpdatesEnabled(updates_enabled)
            self._busy = was_busy

    def switch(self, identifier):
        target = next((view for view in self.views if view["id"] == identifier), None)
        if target is None or target is self.active:
            return
        if self.active:
            self.active["layout"] = self.workspace.capture_layout()
        previous = self.active_id
        self.active_id = identifier
        if not self._apply(target["layout"]):
            self.active_id = previous
            if self.active:
                self._apply(self.active["layout"])
            self.changed.emit()
            return
        self.changed.emit()
        self.save()

    def create(self, name, icon):
        name = self._validate_name(name)
        if self.active:
            self.active["layout"] = self.workspace.capture_layout()
        self._busy = True
        try:
            empty = self.workspace.clear_layout()
        finally:
            self._busy = False
        view = {
            "id": uuid4().hex,
            "name": name,
            "icon": icon,
            "layout": empty,
            "categories": [],
        }
        self.views.append(view)
        self.active_id = view["id"]
        self.changed.emit()
        self.save()
        return view["id"]

    @staticmethod
    def _validate_name(name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("İsim boş olamaz")
        return name.strip()[:80]

    def add_category(self, name, icon, panels):
        if self.active is None:
            raise ValueError("Aktif görünüm yok")
        keys = list(
            dict.fromkeys(
                key
                for key in panels
                if key in self.workspace.panels
                and not self.workspace.panels[key].isHidden()
            )
        )
        if not keys:
            raise ValueError("En az bir açık panel seçin")
        group = {
            "id": uuid4().hex,
            "name": self._validate_name(name),
            "icon": icon,
            "panels": keys,
        }
        self.active["categories"].append(group)
        self.changed.emit()
        self.save()

    def toggle_category(self, identifier):
        if self.active is None:
            return
        group = next(
            (item for item in self.active["categories"] if item["id"] == identifier),
            None,
        )
        if group is None:
            return
        docks = [
            self.workspace.panels[key]
            for key in group["panels"]
            if key in self.workspace.panels
        ]
        if not docks:
            return
        show = not all(not dock.isHidden() for dock in docks)
        # Capture the topology BEFORE closing a group, and restore it only when
        # reopening. Preserve unrelated panels' live visibility, including tabs.
        if not show:
            group["layout"] = self.workspace.capture_layout()
        elif "layout" in group:
            snapshot = deepcopy(group["layout"])
            snapshot["visible_panels"] = list(
                dict.fromkeys(
                    [
                        key
                        for key, dock in self.workspace.panels.items()
                        if not dock.isHidden()
                    ]
                    + group["panels"]
                )
            )
            self._apply(snapshot)
        self._busy = True
        self.workspace._restoring_panel_sizes = True
        try:
            for dock in docks:
                dock.setVisible(show)
        finally:
            self.workspace._restoring_panel_sizes = False
            self._busy = False
        self.changed.emit()
        self.save()

    def remove(self, identifier, *, category=False):
        if category and self.active:
            self.active["categories"] = [
                g for g in self.active["categories"] if g["id"] != identifier
            ]
        elif len(self.views) > 1:
            if identifier == self.active_id:
                self.switch(next(v["id"] for v in self.views if v["id"] != identifier))
            self.views = [v for v in self.views if v["id"] != identifier]
        self.changed.emit()
        self.save()

    def save(self):
        self._save_timer.stop()
        if self._busy or self.active is None:
            return False
        self.active["layout"] = self.workspace.capture_layout()
        return self.workspace.save_layout(
            views={"active": self.active_id, "items": self.views}
        )
