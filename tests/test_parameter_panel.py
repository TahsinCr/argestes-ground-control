from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from extensions.parameters.ui.panel import ParametersPanel
from src.models.parameters import Parameter


_APP = QApplication.instance() or QApplication([])


def parameter(key: str, value: float) -> Parameter:
    return Parameter(
        key=key,
        value=value,
        unit="-",
        category="test",
        description="Test parameter",
        value_type=9,
    )


class ParametersPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = ParametersPanel("", "", "")
        self.panel.set_parameters([parameter("TEST_A", 1.0)])
        self.submitted: list[dict[str, object]] = []
        self.panel.apply_requested.connect(self.submitted.append)

    def tearDown(self) -> None:
        self.panel.close()
        self.panel.deleteLater()

    def test_edit_is_staged_until_apply_and_confirmation(self) -> None:
        self.panel.table.item(0, 1).setText("2.5")

        self.assertEqual(self.submitted, [])
        self.assertEqual(self.panel._dirty_values, {"TEST_A": 2.5})

        self.panel.apply_changes()

        self.assertEqual(self.submitted, [{"TEST_A": 2.5}])
        self.assertTrue(self.panel._write_in_progress)
        self.assertEqual(self.panel._dirty_values, {"TEST_A": 2.5})

        self.panel.finish_apply({"TEST_A": 2.5}, "confirmation timed out")

        self.assertFalse(self.panel._write_in_progress)
        self.assertEqual(self.panel._dirty_values, {"TEST_A": 2.5})
        self.assertEqual(self.panel._original_values["TEST_A"], 1.0)
        self.assertIn("confirmation timed out", self.panel.pending_label.toolTip())

        self.panel.apply_changes()
        self.panel.finish_apply({"TEST_A": 2.5}, None)

        self.assertEqual(self.panel._dirty_values, {})
        self.assertEqual(self.panel._original_values["TEST_A"], 2.5)

    def test_backend_refresh_preserves_unapplied_draft_and_original(self) -> None:
        self.panel.table.item(0, 1).setText("2.5")
        self.panel.set_parameters(
            [parameter("TEST_B", 7.0), parameter("TEST_A", 1.0)]
        )

        row = next(
            row
            for row in range(self.panel.table.rowCount())
            if self.panel.table.item(row, 0).text() == "TEST_A"
        )
        self.assertEqual(self.panel.table.item(row, 1).text(), "2.5")
        self.assertEqual(self.panel._dirty_values, {"TEST_A": 2.5})
        self.assertEqual(self.panel._original_values["TEST_A"], 1.0)

        self.panel.reset_to_originals()
        row = next(
            row
            for row in range(self.panel.table.rowCount())
            if self.panel.table.item(row, 0).text() == "TEST_A"
        )
        self.assertEqual(self.panel.table.item(row, 1).text(), "1.0")
        self.assertEqual(self.panel._dirty_values, {})


if __name__ == "__main__":
    unittest.main()
