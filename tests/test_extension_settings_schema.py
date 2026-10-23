import tempfile
import unittest
from pathlib import Path

from src.extension.api.clocks import ClockReferenceRegistry
from src.extension.api.settings import validate_settings
from src.container import create_container
from src.services.settings import SettingsService


class ExtensionSettingsSchemaTests(unittest.TestCase):
    def test_live_probe_preserves_callback_arguments_and_detaches(self):
        import queue
        from tools.qt_queue_probe import AdapterProbe

        class Adapter:
            def __init__(self):
                self._pending_events = queue.SimpleQueue()

            def _post(self, callback, *args):
                self._pending_events.put((callback, args))

        adapter = Adapter()
        original = adapter._post
        probe = AdapterProbe(adapter, sample_limit=1)
        received = []
        adapter._post(received.append, "payload")
        callback, args = adapter._pending_events.get_nowait()
        callback(*args)
        self.assertEqual(received, ["payload"])
        self.assertEqual(probe.snapshot()["completed"], 1)
        probe.close()
        self.assertEqual(adapter._post, original)

    def test_scoped_clocks_reject_cross_owner_changes(self):
        root = ClockReferenceRegistry()
        first = root._scope("first")
        second = root._scope("second")
        identifier = first.publish("utc", "First", lambda: None)
        for action in (
            lambda: second.register("first", "utc", "Bad", lambda: None),
            lambda: second.unregister(identifier),
            lambda: second.unregister_owner("first"),
            lambda: second._scope("first"),
        ):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(root.references()[0].title, "First")

    def test_nested_schema_errors_include_path(self):
        schema = {"type": "object", "properties": {"items": {
            "type": "array", "items": {"type": "object", "required": ["port"],
            "properties": {"port": {"type": "integer", "minimum": 1}}}}}}
        for value in ({"items": [{}]}, {"items": [{"port": True}]},
                      {"items": [{"port": 0}]}):
            with self.assertRaisesRegex(ValueError, r"config.items\[0\].port"):
                validate_settings(value, schema)

    def test_direct_camera_settings_writer_rejects_bad_nested_type(self):
        with tempfile.TemporaryDirectory() as directory:
            container = create_container(Path(directory))
            try:
                settings = container.resolve(SettingsService)
                before = settings.extension_config("camera")
                with self.assertRaisesRegex(ValueError, "source_uri"):
                    settings.update_extension("camera", config={"video_sources": [
                        {"source_uri": 123}]})
                self.assertEqual(settings.extension_config("camera"), before)
            finally:
                container.shutdown()
