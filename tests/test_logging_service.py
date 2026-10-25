from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services.logging_service import LoggingService


class LoggingServiceTests(unittest.TestCase):
    def test_persistence_failure_is_visible_in_memory_and_does_not_escape(self) -> None:
        with TemporaryDirectory() as directory:
            service = LoggingService(log_path=Path(directory) / "session.json")
            delivered = []
            service.entries.subscribe(delivered.append)

            with patch.object(
                service, "_persist_many", side_effect=OSError("disk full")
            ):
                service.log("COMMAND", "first")
                service.log("COMMAND", "second")

            failures = [
                entry
                for entry in delivered
                if entry.action == "logging.persistence_failed"
            ]
            self.assertEqual(len(failures), 1)
            self.assertIn("yalnızca bellekte", failures[0].message)
            self.assertEqual(
                [
                    entry.message
                    for entry in service.history
                    if entry.source == "COMMAND"
                ],
                ["first", "second"],
            )

    def test_bounded_history_keeps_the_newest_entries_in_order(self) -> None:
        service = LoggingService(max_entries=3)

        for index in range(5):
            service.log("TEST", f"Entry {index}")

        self.assertEqual(
            [entry.message for entry in service.history],
            ["Entry 2", "Entry 3", "Entry 4"],
        )

    def test_session_files_rotate_with_valid_json_and_bounded_backups(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gcs_test.json"
            service = LoggingService(
                log_path=path,
                max_file_bytes=180,
                backup_count=2,
            )

            for index in range(12):
                service.log("TEST", f"Entry {index}: " + "x" * 40)

            retained = [
                path,
                path.with_name(path.name + ".1"),
                path.with_name(path.name + ".2"),
            ]
            for rotated in retained:
                self.assertTrue(rotated.exists())
                self.assertIsInstance(json.loads(rotated.read_text()), list)
            self.assertFalse(path.with_name(path.name + ".3").exists())

    def test_partial_json_append_failure_preserves_previous_records(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            service = LoggingService(log_path=path)
            service.log("TEST", "previous record")
            original_open = Path.open
            injected = False

            class PartialWriter:
                def __init__(self, handle) -> None:
                    self.handle = handle

                def __enter__(self):
                    self.handle.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.handle.__exit__(*args)

                def __getattr__(self, name):
                    return getattr(self.handle, name)

                def write(self, data):
                    nonlocal injected
                    if not injected:
                        injected = True
                        self.handle.write(data[: max(1, len(data) // 2)])
                        raise OSError("simulated partial disk write")
                    return self.handle.write(data)

            def open_with_partial_write(target, *args, **kwargs):
                handle = original_open(target, *args, **kwargs)
                if target == path and args and args[0] == "r+b":
                    return PartialWriter(handle)
                return handle

            with patch.object(Path, "open", new=open_with_partial_write):
                service.log("TEST", "interrupted record")

            records = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([record["message"] for record in records], ["previous record"])
            self.assertTrue(injected)

    def test_storage_manager_exports_and_deletes_only_archived_session_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "gcs_2026-10-24_12-00-00.json"
            archived = root / "gcs_2026-10-23_12-00-00.json"
            service = LoggingService(log_path=active)
            service.log("TEST", "active")
            archived.write_text('[{"message":"archived"}]', encoding="utf-8")

            summary = service.storage_summary()
            self.assertEqual(summary["file_count"], 2)
            self.assertEqual(summary["archived_count"], 1)
            self.assertEqual(summary["total_bytes"], active.stat().st_size + archived.stat().st_size)
            with self.assertRaisesRegex(ValueError, "current session"):
                service.delete_archived_log(active.name)

            with TemporaryDirectory() as export_directory:
                exported = Path(export_directory) / "export.json"
                service.export_log(active.name, exported)
                self.assertEqual(
                    json.loads(exported.read_text()), json.loads(active.read_text())
                )
            archived_size = archived.stat().st_size
            self.assertEqual(service.delete_archived_log(archived.name), archived_size)
            self.assertFalse(archived.exists())
            self.assertEqual(service.storage_summary()["archived_count"], 0)


if __name__ == "__main__":
    unittest.main()
