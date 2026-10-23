from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from extensions.parameters.ui.panel import ParametersPanel
from src.models.parameters import Parameter


_APP = QApplication.instance() or QApplication(["yki-parameter-tests"])


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

    def test_invalid_values_remain_visible_and_block_apply_until_corrected(self) -> None:
        self.panel.set_parameters(
            [parameter("INTEGER", 1), parameter("FLAG", False), parameter("REAL", 1.0)]
        )

        def row(key: str) -> int:
            return next(
                index
                for index in range(self.panel.table.rowCount())
                if self.panel.table.item(index, 0).text() == key
            )

        self.panel.table.item(row("INTEGER"), 1).setText("1.7")
        self.panel.table.item(row("FLAG"), 1).setText("maybe")
        self.panel.table.item(row("REAL"), 1).setText("nan")

        self.assertEqual(self.panel.table.item(row("INTEGER"), 1).text(), "1.7")
        self.assertEqual(self.panel._validation_errors.keys(), {"INTEGER", "FLAG", "REAL"})
        self.assertFalse(self.panel.apply_button.isEnabled())
        self.assertTrue(self.panel.reset_button.isEnabled())
        self.panel.apply_changes()
        self.assertEqual(self.submitted, [])

        self.panel.reset_to_originals()
        self.assertEqual(self.panel._validation_errors, {})
        self.assertEqual(self.panel._invalid_values, {})

    def test_integer_input_does_not_truncate_decimal_text(self) -> None:
        self.panel.set_parameters([parameter("COUNT", 1)])
        self.panel.table.item(0, 1).setText("3")
        self.assertEqual(self.panel._dirty_values, {"COUNT": 3})
        self.panel.table.item(0, 1).setText("3.9")
        self.assertEqual(self.panel._dirty_values, {"COUNT": 3})
        self.assertIn("COUNT", self.panel._validation_errors)

    def test_partial_write_confirmation_clears_only_confirmed_draft(self) -> None:
        self.panel.set_parameters([parameter("FIRST", 1.0), parameter("SECOND", 2.0)])
        self.panel.table.item(0, 1).setText("3.0")
        self.panel.table.item(1, 1).setText("4.0")
        changes = dict(self.panel._dirty_values)
        self.panel.apply_changes()

        error = RuntimeError("one result is uncertain")
        error.confirmed_values = {"FIRST": 3.0}
        self.panel.finish_apply(changes, error)

        self.assertEqual(self.panel._original_values["FIRST"], 3.0)
        self.assertEqual(self.panel._dirty_values, {"SECOND": 4.0})


if __name__ == "__main__":
    unittest.main()
