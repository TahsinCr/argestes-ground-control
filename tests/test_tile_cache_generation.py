from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from extensions.map_autonomy.services.tile_cache import TileCacheServer, TileRequest


class TileCacheGenerationTests(unittest.TestCase):
    PNG_HEADER = b"\x89PNG\r\n\x1a\n"

    def test_fetch_from_before_clear_cannot_repopulate_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = TileCacheServer(
                cache_path=directory,
                enabled=True,
                limit_mb=4,
            )
            tile = TileRequest("provider", "default", 1, 0, 0, "png")
            request_generation = server._generation

            server.clear()
            server._write_tile(tile, b"stale", generation=request_generation)

            self.assertEqual(server.count_tiles(), 0)
            self.assertFalse(server._tile_path(tile).exists())
            server.stop()

    def test_missing_or_empty_cached_tile_removes_its_index_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = TileCacheServer(
                cache_path=directory,
                enabled=True,
                limit_mb=4,
            )
            try:
                for x, damage in ((1, "missing"), (2, "empty")):
                    with self.subTest(damage=damage):
                        tile = TileRequest("provider", "default", 1, x, 0, "png")
                        path = server._tile_path(tile)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"cached tile payload")
                        server._touch_index(tile, path, persist=True)

                        self.assertEqual(server.count_tiles(), 1)
                        self.assertEqual(server._total_bytes, len(b"cached tile payload"))
                        if damage == "missing":
                            path.unlink()
                        else:
                            path.write_bytes(b"")

                        self.assertIsNone(server._read_cached_tile(tile))
                        self.assertNotIn(tile.key, server._index)
                        self.assertEqual(server.count_tiles(), 0)
                        self.assertEqual(server._total_bytes, 0)
            finally:
                server.stop()

    def test_cache_hit_started_before_clear_cannot_restore_index_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = TileCacheServer(cache_path=directory, enabled=True, limit_mb=4)
            tile = TileRequest("esri", "world-imagery", 0, 0, 0, "png")
            path = server._tile_path(tile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.PNG_HEADER)
            server._touch_index(tile, path, persist=True)

            touch_started = threading.Event()
            continue_touch = threading.Event()
            original_touch = server._touch_index

            def pause_before_index_update(*args, **kwargs) -> None:
                if not kwargs.get("persist"):
                    touch_started.set()
                    if not continue_touch.wait(1.0):
                        raise TimeoutError("test did not release cache-hit barrier")
                original_touch(*args, **kwargs)

            server._touch_index = pause_before_index_update
            result: list[bytes | None] = []
            reader = threading.Thread(
                target=lambda: result.append(server._read_cached_tile(tile))
            )
            reader.start()
            try:
                self.assertTrue(touch_started.wait(1.0))
                server.clear()
            finally:
                continue_touch.set()
                reader.join(1.0)
                server.stop()

            self.assertFalse(reader.is_alive())
            self.assertEqual(result, [self.PNG_HEADER])
            self.assertEqual(server.count_tiles(), 0)
            self.assertEqual(server._total_bytes, 0)
            self.assertNotIn(tile.key, server._index)

    def test_old_fetch_cannot_write_to_cache_after_cache_root_switch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = f"{root}/first"
            second = f"{root}/second"
            server = TileCacheServer(cache_path=first, enabled=True, limit_mb=4)
            fetch_started = threading.Event()
            continue_fetch = threading.Event()

            def delayed_fetch(_tile):
                fetch_started.set()
                if not continue_fetch.wait(1.0):
                    raise TimeoutError("test did not release remote-fetch barrier")
                return self.PNG_HEADER, "image/png"

            server._fetch_remote_tile = delayed_fetch
            responses = []
            request = threading.Thread(
                target=lambda: responses.append(
                    server.handle_request("/tile/esri/world-imagery/0/0/0.png")
                )
            )
            request.start()
            try:
                self.assertTrue(fetch_started.wait(1.0))
                server.configure(cache_path=second, enabled=True, limit_mb=4)
            finally:
                continue_fetch.set()
                request.join(1.0)
                server.stop()

            self.assertFalse(request.is_alive())
            self.assertEqual(len(responses), 1)
            self.assertTrue(responses[0].payload)
            self.assertEqual(server.cache_path.as_posix(), second)
            self.assertEqual(server.count_tiles(), 0)
            self.assertEqual(server._index, {})
            self.assertFalse(server._tile_path(
                TileRequest("esri", "world-imagery", 0, 0, 0, "png")
            ).exists())

    def test_new_cache_generation_does_not_wait_for_an_old_tile_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "first"
            second = Path(root) / "second"
            server = TileCacheServer(cache_path=first, enabled=True, limit_mb=4)
            first_fetch_started = threading.Event()
            release_first_fetch = threading.Event()
            second_fetch_started = threading.Event()
            fetch_lock = threading.Lock()
            fetch_count = 0

            def fetch(_tile):
                nonlocal fetch_count
                with fetch_lock:
                    fetch_count += 1
                    current_fetch = fetch_count
                if current_fetch == 1:
                    first_fetch_started.set()
                    if not release_first_fetch.wait(2.0):
                        raise TimeoutError("test did not release the old tile fetch")
                else:
                    second_fetch_started.set()
                return self.PNG_HEADER, "image/png"

            server._fetch_remote_tile = fetch
            old_result = []
            old_request = threading.Thread(
                target=lambda: old_result.append(
                    server.handle_request("/tile/esri/world-imagery/0/0/0.png")
                )
            )
            old_request.start()
            try:
                self.assertTrue(first_fetch_started.wait(1.0))
                server.configure(cache_path=second, enabled=True, limit_mb=4)
                new_result = []
                new_request = threading.Thread(
                    target=lambda: new_result.append(
                        server.handle_request("/tile/esri/world-imagery/0/0/0.png")
                    )
                )
                new_request.start()
                fetched_without_old = second_fetch_started.wait(0.5)
                release_first_fetch.set()
                new_request.join(1.0)
                old_request.join(1.0)

                self.assertTrue(fetched_without_old)
                self.assertFalse(new_request.is_alive())
                self.assertFalse(old_request.is_alive())
                self.assertEqual(new_result[0].status, 200)
                self.assertEqual(old_result[0].status, 200)
                self.assertEqual(server.count_tiles(), 1)
            finally:
                release_first_fetch.set()
                old_request.join(1.0)
                server.stop()

    def test_invalid_tile_read_cannot_delete_a_new_atomic_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = TileCacheServer(cache_path=directory, enabled=True, limit_mb=4)
            tile = TileRequest("esri", "world-imagery", 0, 0, 0, "png")
            path = server._tile_path(tile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"corrupt tile")
            server._touch_index(tile, path, persist=True)

            read_finished = threading.Event()
            release_reader = threading.Event()
            original_read_bytes = Path.read_bytes

            def pause_after_read(candidate: Path) -> bytes:
                payload = original_read_bytes(candidate)
                if candidate == path:
                    read_finished.set()
                    if not release_reader.wait(2.0):
                        raise TimeoutError("test did not release the invalid tile read")
                return payload

            result = []
            reader = threading.Thread(
                target=lambda: result.append(server._read_cached_tile(tile))
            )
            with patch.object(Path, "read_bytes", pause_after_read):
                reader.start()
                try:
                    self.assertTrue(read_finished.wait(1.0))
                    server._write_tile(tile, self.PNG_HEADER, generation=server._generation)
                finally:
                    release_reader.set()
                    reader.join(1.0)
                    server.stop()

            self.assertFalse(reader.is_alive())
            self.assertEqual(result, [None])
            self.assertEqual(path.read_bytes(), self.PNG_HEADER)
            self.assertIn(tile.key, server._index)

    def test_clean_cache_index_skips_tree_scan_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tile = TileRequest("esri", "world-imagery", 0, 0, 0, "png")
            server = TileCacheServer(cache_path=directory, enabled=True, limit_mb=4)
            server._write_tile(tile, self.PNG_HEADER, generation=server._generation)
            server.stop()

            with patch.object(Path, "rglob", side_effect=AssertionError("unexpected scan")):
                reopened = TileCacheServer(
                    cache_path=directory, enabled=True, limit_mb=4
                )
                try:
                    self.assertEqual(reopened.count_tiles(), 1)
                    result = reopened.handle_request(
                        "/tile/esri/world-imagery/0/0/0.png"
                    )
                    self.assertEqual(result.cache_state, "hit")
                finally:
                    reopened.stop()

    def test_dirty_cache_index_rebuild_finds_tiles_missing_from_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = TileCacheServer(cache_path=directory, enabled=True, limit_mb=4)
            first_tile = TileRequest("esri", "world-imagery", 1, 0, 0, "png")
            server._write_tile(
                first_tile, self.PNG_HEADER, generation=server._generation
            )
            server.stop()

            second_tile = TileRequest("esri", "world-imagery", 1, 1, 0, "png")
            second_path = server._tile_path(second_tile)
            second_path.parent.mkdir(parents=True, exist_ok=True)
            second_path.write_bytes(self.PNG_HEADER)
            server.index_dirty_path.touch()

            reopened = TileCacheServer(
                cache_path=directory, enabled=True, limit_mb=4
            )
            try:
                self.assertEqual(reopened.count_tiles(), 2)
                self.assertFalse(reopened.index_dirty_path.exists())
            finally:
                reopened.stop()

    def test_entries_returns_copies_of_index_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = TileCacheServer(cache_path=directory, enabled=True, limit_mb=4)
            try:
                tile = TileRequest("esri", "world-imagery", 0, 0, 0, "png")
                path = server._tile_path(tile)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.PNG_HEADER)
                server._touch_index(tile, path, persist=True)

                entry = server.entries()[0]
                entry["size"] = 0
                entry["provider"] = "mutated"

                self.assertEqual(server._index[tile.key]["size"], len(self.PNG_HEADER))
                self.assertEqual(server._index[tile.key]["provider"], "esri")
                self.assertEqual(server._total_bytes, len(self.PNG_HEADER))
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
