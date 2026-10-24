from __future__ import annotations

import tempfile
import unittest

from extensions.map_autonomy.services.tile_cache import TileCacheServer, TileRequest


class TileCacheGenerationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
