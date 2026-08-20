from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui.i18n import t
from src.ui.widgets.common import PanelFrame, compact_button


class TargetSelectionPanel(PanelFrame):
    """Operator-facing view for the ranked combat-target candidates.

    The panel intentionally keeps the table compact and places secondary
    diagnostics in a scrollable detail area.  This lets it survive the same
    QSplitter resizing used by the Parameters tab without imposing a large
    minimum size on the workspace.
    """

    rank_requested = pyqtSignal()
    preview_requested = pyqtSignal(object)
    activate_requested = pyqtSignal(int)
    cancel_requested = pyqtSignal()

    def __init__(self, title: str = "", subtitle: str = "", parent=None) -> None:
        super().__init__(title, subtitle, parent)
        self._language = "tr"
        self._candidates: list[object] = []
        self._selected_team: int | None = None
        self._active_team: int | None = None
        self._busy = False
        self._operation_busy = False
        self._last_elapsed_ms = 0.0

        self.setMinimumWidth(0)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # PanelFrame already owns the fixed title area.  Everything below it is
        # scrollable so reducing the lower splitter never clips the controls.
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("CommandTabScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.content = QWidget(self.scroll)
        self.content.setObjectName("CommandTabContent")
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        top_row = QWidget(self.content)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        self.rank_button = compact_button("AV SEÇ", "primary")
        self.rank_button.clicked.connect(self.rank_requested.emit)
        top_layout.addWidget(self.rank_button)
        self.status_label = QLabel(self.content)
        self.status_label.setProperty("role", "note")
        self.status_label.setWordWrap(True)
        top_layout.addWidget(self.status_label, 1)
        content_layout.addWidget(top_row)

        self.table = QTableWidget(0, 4, self.content)
        self.table.setObjectName("TargetSelectionTable")
        self.table.setHorizontalHeaderLabels(["", "", "", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setMinimumHeight(112)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        content_layout.addWidget(self.table, 1)

        self.detail_label = QLabel(self.content)
        self.detail_label.setObjectName("TargetSelectionDetails")
        self.detail_label.setProperty("role", "note")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_layout.addWidget(self.detail_label)

        self.reason_label = QLabel(self.content)
        self.reason_label.setObjectName("TargetSelectionReason")
        self.reason_label.setWordWrap(True)
        self.reason_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content_layout.addWidget(self.reason_label)

        action_row = QWidget(self.content)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        self.active_label = QLabel(self.content)
        self.active_label.setProperty("role", "note")
        self.active_label.setWordWrap(True)
        action_layout.addWidget(self.active_label, 1)
        self.cancel_button = compact_button("İPTAL")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        action_layout.addWidget(self.cancel_button)
        self.activate_button = compact_button("SEÇ", "primary")
        self.activate_button.clicked.connect(self._activate_selected)
        action_layout.addWidget(self.activate_button)
        content_layout.addWidget(action_row)

        self.scroll.setWidget(self.content)
        self.body_layout.addWidget(self.scroll, 1)
        self.set_language(self._language)
        self._refresh_controls()

    @property
    def selected_team(self) -> int | None:
        return self._selected_team

    def set_language(self, language: str) -> None:
        self._language = language
        self.rank_button.setText(t("target_selection.rank", language))
        self.activate_button.setText(t("target_selection.activate", language))
        self.cancel_button.setText(t("target_selection.cancel", language))
        self.table.setHorizontalHeaderLabels(
            [
                t("target_selection.column.team", language),
                t("target_selection.column.score", language),
                t("target_selection.column.lock", language),
                t("target_selection.column.eta", language),
            ]
        )
        self._render_status()
        self._render_selected_details()
        self._render_active_label()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._refresh_controls()
        self._render_status()

    def set_operation_busy(self, busy: bool) -> None:
        self._operation_busy = bool(busy)
        self._refresh_controls()

    def set_results(self, result) -> None:
        self._busy = False
        self._candidates = list(getattr(result, "candidates", ()) or ())[:5]
        previous_team = self._selected_team
        self._selected_team = None

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for candidate in self._candidates:
            row = self.table.rowCount()
            self.table.insertRow(row)
            team = int(getattr(candidate, "team_number", 0))
            score = float(getattr(candidate, "final_score", 0.0))
            probability = float(getattr(candidate, "lock_probability", 0.0))
            eta = float(getattr(candidate, "merge_eta_s", 0.0))
            cells = (
                QTableWidgetItem(str(team)),
                QTableWidgetItem(f"{score:.0f}/100"),
                QTableWidgetItem(f"%{probability:.0f}"),
                QTableWidgetItem(f"{eta:.1f} s"),
            )
            for column, item in enumerate(cells):
                item.setData(Qt.ItemDataRole.UserRole, team)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)

        if self._candidates:
            row = 0
            if previous_team is not None:
                for index, candidate in enumerate(self._candidates):
                    if int(getattr(candidate, "team_number", 0)) == previous_team:
                        row = index
                        break
            self.table.selectRow(row)
            self._select_row(row, emit_preview=True)
        else:
            self.table.clearSelection()
            self.preview_requested.emit(None)

        elapsed = float(getattr(result, "elapsed_ms", 0.0))
        self._last_elapsed_ms = elapsed
        considered = int(getattr(result, "considered_count", 0))
        valid = int(getattr(result, "valid_count", 0))
        simulated = int(getattr(result, "simulated_count", 0))
        self.status_label.setToolTip(
            t("target_selection.run_summary", self._language).format(
                considered=considered,
                valid=valid,
                simulated=simulated,
                elapsed=elapsed,
            )
        )
        self._refresh_controls()
        self._render_status(elapsed_ms=elapsed)
        self._render_selected_details()

    def set_error(self, message: str) -> None:
        self._busy = False
        self.status_label.setText(
            t("target_selection.failed", self._language).format(error=message)
        )
        self.status_label.setProperty("severity", "danger")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._refresh_controls()

    def set_active_team(self, team_number: int | None) -> None:
        self._active_team = int(team_number) if team_number is not None else None
        self._render_active_label()
        self._refresh_controls()

    def set_operation_result(self, success: bool, message: str) -> None:
        self._operation_busy = False
        self._refresh_controls()
        if message:
            self.status_label.setText(message)
            self.status_label.setProperty("severity", "success" if success else "danger")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            self._selected_team = None
            self.preview_requested.emit(None)
            self._render_selected_details()
            self._refresh_controls()
            return
        self._select_row(rows[0].row(), emit_preview=True)

    def _select_row(self, row: int, *, emit_preview: bool) -> None:
        if row < 0 or row >= len(self._candidates):
            return
        candidate = self._candidates[row]
        self._selected_team = int(getattr(candidate, "team_number", 0))
        if emit_preview:
            self.preview_requested.emit(self._selected_team)
        self._render_selected_details()
        self._refresh_controls()

    def _activate_selected(self) -> None:
        if self._selected_team is None or self._operation_busy:
            return
        self._operation_busy = True
        self._refresh_controls()
        self.activate_requested.emit(self._selected_team)

    def _selected_candidate(self):
        if self._selected_team is None:
            return None
        for candidate in self._candidates:
            if int(getattr(candidate, "team_number", 0)) == self._selected_team:
                return candidate
        return None

    def _render_status(self, *, elapsed_ms: float | None = None) -> None:
        self.status_label.setProperty("severity", "")
        if self._busy:
            text = t("target_selection.running", self._language)
        elif self._candidates:
            elapsed = self._last_elapsed_ms if elapsed_ms is None else elapsed_ms
            text = t("target_selection.ready", self._language).format(
                count=len(self._candidates), elapsed=elapsed
            )
        else:
            text = t("target_selection.empty", self._language)
        self.status_label.setText(text)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _render_selected_details(self) -> None:
        candidate = self._selected_candidate()
        if candidate is None:
            self.detail_label.setText(t("target_selection.details.empty", self._language))
            self.reason_label.setText("")
            return

        tail = float(getattr(candidate, "tail_access_score", 0.0))
        distance = float(getattr(candidate, "distance_m", 0.0))
        altitude = float(getattr(candidate, "altitude_delta_m", 0.0))
        age = int(getattr(candidate, "data_age_ms", 0))
        turn = float(getattr(candidate, "turn_required_deg", 0.0))
        quality = float(getattr(candidate, "simulation_quality", 0.0))
        best_start_value = getattr(candidate, "best_lock_start_s", None)
        best_start = (
            f"{float(best_start_value):.1f} s"
            if best_start_value is not None
            else "—"
        )
        best_duration = float(getattr(candidate, "best_lock_duration_s", 0.0))
        self.detail_label.setText(
            t("target_selection.details", self._language).format(
                tail=tail,
                turn=turn,
                distance=distance,
                altitude=altitude,
                age=age,
                quality=quality,
                start=best_start,
                duration=best_duration,
            )
        )
        reason = str(getattr(candidate, "reason", "")).strip()
        self.reason_label.setText(
            t("target_selection.reason", self._language).format(reason=reason)
            if reason
            else ""
        )

    def _render_active_label(self) -> None:
        if self._active_team is None:
            self.active_label.setText(t("target_selection.active.none", self._language))
        else:
            self.active_label.setText(
                t("target_selection.active.team", self._language).format(
                    team=self._active_team
                )
            )

    def _refresh_controls(self) -> None:
        self.rank_button.setEnabled(not self._busy and not self._operation_busy)
        self.activate_button.setEnabled(
            self._selected_team is not None and not self._busy and not self._operation_busy
        )
        self.cancel_button.setEnabled(
            self._active_team is not None and not self._operation_busy
        )
