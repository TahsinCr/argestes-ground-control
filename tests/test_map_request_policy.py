from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PyQt6.QtCore import QUrl

from extensions.map_autonomy.ui.panel import _map_request_allowed


class MapRequestPolicyTests(unittest.TestCase):
    def test_only_packaged_map_assets_are_readable_as_file_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "web"
            vendor = root / "vendor"
            vendor.mkdir(parents=True)
            document = root / "map.html"
            document.write_text("<html></html>", encoding="utf-8")
            asset = vendor / "leaflet.js"
            asset.write_text("window.L = {};", encoding="utf-8")
            secret = Path(directory) / "private.txt"
            secret.write_text("private", encoding="utf-8")

            def allowed(path: Path, *, main_frame: bool = False) -> bool:
                return _map_request_allowed(
                    QUrl.fromLocalFile(str(path)),
                    web_root=root,
                    map_document=document,
                    tile_cache_url="http://127.0.0.1:43817",
                    main_frame=main_frame,
                )

            self.assertTrue(allowed(document, main_frame=True))
            self.assertFalse(allowed(asset, main_frame=True))
            self.assertTrue(allowed(asset))
            self.assertFalse(allowed(secret))

            escaped = vendor / "outside-link"
            try:
                escaped.symlink_to(secret)
            except OSError:  # pragma: no cover - restricted Windows environments
                pass
            else:
                self.assertFalse(allowed(escaped))

    def test_network_access_is_limited_to_tile_origins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "map.html"
            document.write_text("<html></html>", encoding="utf-8")

            def allowed(url: str, *, main_frame: bool = False) -> bool:
                return _map_request_allowed(
                    QUrl(url),
                    web_root=root,
                    map_document=document,
                    tile_cache_url="http://127.0.0.1:43817",
                    main_frame=main_frame,
                )

            self.assertTrue(
                allowed("https://a.basemaps.cartocdn.com/rastertiles/voyager/2/1/1.png")
            )
            self.assertTrue(
                allowed("https://server.arcgisonline.com/ArcGIS/rest/services/tile.png")
            )
            self.assertTrue(allowed("http://127.0.0.1:43817/tile/carto/voyager/2/1/1.png"))
            self.assertFalse(allowed("http://127.0.0.1:43817/health"))
            self.assertFalse(allowed("http://127.0.0.1:43818/tile/carto/voyager/2/1/1.png"))
            self.assertFalse(allowed("https://example.com/tile.png"))
            self.assertFalse(allowed("javascript:alert(1)"))
            self.assertTrue(allowed("data:,"))
            self.assertTrue(
                allowed("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>")
            )
            self.assertFalse(allowed("data:text/html,<script>alert(1)</script>"))
            self.assertFalse(allowed("blob:https://example.com/resource"))
            self.assertFalse(
                allowed("https://a.basemaps.cartocdn.com/tile.png", main_frame=True)
            )
            self.assertTrue(allowed("qrc:///qtwebchannel/qwebchannel.js"))
            self.assertFalse(allowed("qrc:///qt.conf"))


if __name__ == "__main__":
    unittest.main()
