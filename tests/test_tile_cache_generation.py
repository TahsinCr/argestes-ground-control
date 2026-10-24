from __future__ import annotations

import tempfile
import threading
import unittest

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
