from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import monitor_performance


class PerformanceMonitorTests(unittest.TestCase):
    def test_linux_sample_reports_pss_and_rss_for_current_process(self) -> None:
        records = monitor_performance._sample(os.getpid())
        current = next(item for item in records if item.pid == os.getpid())

        self.assertGreater(current.cpu_seconds, 0.0)
        self.assertGreater(current.pss_bytes, 0)
        self.assertGreaterEqual(current.rss_bytes, current.pss_bytes)
        self.assertEqual(current.role, "yki")

    def test_process_tree_contains_its_root(self) -> None:
        self.assertIn(os.getpid(), monitor_performance._process_tree(os.getpid()))

    def test_process_exit_during_memory_sample_is_ignored(self) -> None:
        with patch(
            "monitor_performance._memory_bytes",
            side_effect=ProcessLookupError("process exited"),
        ):
            records = monitor_performance._sample(os.getpid())

        self.assertEqual(records, ())


if __name__ == "__main__":
    unittest.main()
