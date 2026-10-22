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


if __name__ == "__main__":
    unittest.main()
