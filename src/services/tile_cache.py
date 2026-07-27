from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from src.core.events import EventStream
from src.services.contracts.tile_cache import TileCacheService
from src.services.storage import ensure_directory


BLANK_TILE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
    b'<rect width="256" height="256" fill="#11171d"/></svg>'
)

CARTO_VARIANTS = {
    "dark_nolabels",
    "dark_only_labels",
    "light_nolabels",
    "light_only_labels",
}


@dataclass(frozen=True, slots=True)
class TileRequest:
    provider: str
    variant: str
    z: int
    x: int
    y: int
    extension: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.variant}:{self.z}:{self.x}:{self.y}:{self.extension}"


class TileResult(NamedTuple):
    status: int
    content_type: str
    payload: bytes
    cache_state: str
    redirect_url: str | None = None


class _TileHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    def __init__(self, *args: Any, max_workers: int = 8, **kwargs: Any) -> None:
        self._worker_gate = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_gate.acquire(timeout=0.25):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_gate.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_gate.release()


class _TileHandler(BaseHTTPRequestHandler):
    server_version = "YKITileCache/2.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        result = self.server.cache.handle_request(self.path)  # type: ignore[attr-defined]
        self.send_response(result.status)
        if result.redirect_url:
            self.send_header("Location", result.redirect_url)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Content-Length", str(len(result.payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-YKI-Tile-Cache", result.cache_state)
        self.end_headers()
        try:
            self.wfile.write(result.payload)
        except (BrokenPipeError, ConnectionResetError):
            # WebEngine may cancel an in-flight tile request while shutting down.
            return

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class TileCacheServer(TileCacheService):
    """Cache-first local tile server used by Leaflet.

    Leaflet only talks to this local server when map cache is enabled. The
    server is the single owner of tile persistence: check disk first, fetch the
    missing tile once, write it atomically, then answer the browser.
    """

    def __init__(
        self,
        *,
        cache_path: str | Path,
        enabled: bool,
        limit_mb: int,
        parent: object | None = None,
    ) -> None:
        del parent
        self._cache_changed = EventStream[int]()
        self._cache_path = Path(cache_path)
        self._enabled = enabled
        self._limit_mb = limit_mb
        self._server: _TileHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._fetch_gate = threading.BoundedSemaphore(6)
        self._last_persist_at = 0.0
        self._writes_since_persist = 0
        self._load_from_disk()

    @property
    def cache_changed(self) -> EventStream[int]:
        return self._cache_changed

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def base_url(self) -> str:
        if not self._server:
            return ""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def index_path(self) -> Path:
        return self._cache_path / "cache-index.json"

    def configure(self, *, cache_path: str | Path, enabled: bool, limit_mb: int) -> None:
        with self._lock:
            self._cache_path = Path(cache_path)
            self._enabled = enabled
            self._limit_mb = limit_mb
            self._load_from_disk_locked()
        if not enabled:
            self.stop()
        self.cache_changed.publish(self.count_tiles())

    def start(self) -> None:
        with self._lock:
            if not self._enabled or self._server:
                return
            try:
                server = _TileHttpServer(("127.0.0.1", 0), _TileHandler)
            except OSError:
                # A hardened runtime may forbid local sockets. The map already
                # falls back to direct provider URLs when base_url is empty, so
                # cache availability must never prevent the UI from opening.
                self._server = None
                self._thread = None
                return
            server.cache = self  # type: ignore[attr-defined]
            thread = threading.Thread(
                target=server.serve_forever,
                name="YKITileCache",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._persist_index_locked()
            if self._server is server:
                self._server = None
            if self._thread is thread:
                self._thread = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def clear(self) -> None:
        with self._lock:
            shutil.rmtree(self._tiles_root, ignore_errors=True)
            self._index.clear()
            self.index_path.unlink(missing_ok=True)
        self.cache_changed.publish(0)

    def count_tiles(self) -> int:
        with self._lock:
            return len(self._index)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                self._index.values(),
                key=lambda item: (item.get("provider", ""), item.get("variant", ""), item.get("z", 0), item.get("x", 0), item.get("y", 0)),
            )

    def handle_request(self, raw_path: str) -> TileResult:
        if urlparse(raw_path).path == "/health":
            return TileResult(200, "text/plain; charset=utf-8", b"ok", "health")

        tile = self._parse_tile_request(raw_path)
        if tile is None:
            return TileResult(404, "image/svg+xml", BLANK_TILE, "invalid")

        cached = self._read_cached_tile(tile)
        if cached is not None:
            return TileResult(200, self._content_type(tile.extension), cached, "hit")

        owns_fetch = False
        if self._enabled:
            wait_event = self._begin_fetch(tile.key)
            if wait_event is not None:
                wait_event.wait(timeout=9)
                cached = self._read_cached_tile(tile)
                if cached is not None:
                    return TileResult(200, self._content_type(tile.extension), cached, "hit-after-wait")
            else:
                owns_fetch = True

        try:
            payload, content_type = self._fetch_remote_tile(tile)
            if payload:
                if self._enabled:
                    self._write_tile(tile, payload)
                return TileResult(
                    200,
                    content_type or self._content_type(tile.extension),
                    payload,
                    "miss-stored" if self._enabled else "miss",
                )
        finally:
            if owns_fetch:
                self._finish_fetch(tile.key)

        remote_url = self._remote_url(tile)
        if remote_url:
            return TileResult(302, "text/plain; charset=utf-8", b"", "miss-redirect-fallback", remote_url)
        return TileResult(504, "image/svg+xml", BLANK_TILE, "miss-failed")

    @property
    def _tiles_root(self) -> Path:
        return self._cache_path / "tiles"

    def _begin_fetch(self, key: str) -> threading.Event | None:
        with self._lock:
            event = self._inflight.get(key)
            if event is not None:
                return event
            self._inflight[key] = threading.Event()
            return None

    def _finish_fetch(self, key: str) -> None:
        with self._lock:
            event = self._inflight.pop(key, None)
            if event is not None:
                event.set()

    def _parse_tile_request(self, raw_path: str) -> TileRequest | None:
        parts = [unquote(part) for part in urlparse(raw_path).path.strip("/").split("/") if part]
        if len(parts) != 6 or parts[0] != "tile":
            return None
        _, provider, variant, z_text, x_text, filename = parts
        if not self._safe_component(provider) or not self._safe_component(variant) or "." not in filename:
            return None
        y_text, extension = filename.rsplit(".", 1)
        try:
            z = int(z_text)
            x = int(x_text)
            y = int(y_text)
        except ValueError:
            return None
        extension = extension.lower()
        if extension == "jpeg":
            extension = "jpg"
        if extension not in {"png", "jpg"}:
            return None
        if z < 0 or z > 21 or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
            return None
        if provider == "carto" and variant not in CARTO_VARIANTS:
            return None
        if provider == "esri" and variant != "world-imagery":
            return None
        if provider not in {"carto", "esri"}:
            return None
        return TileRequest(provider=provider, variant=variant, z=z, x=x, y=y, extension=extension)

    @staticmethod
    def _safe_component(value: str) -> bool:
        return bool(value) and all(char.isalnum() or char in {"-", "_"} for char in value)

    def _tile_path(self, tile: TileRequest) -> Path:
        return self._tiles_root / tile.provider / tile.variant / str(tile.z) / str(tile.x) / f"{tile.y}.{tile.extension}"

    def _read_cached_tile(self, tile: TileRequest) -> bytes | None:
        path = self._tile_path(tile)
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                path.unlink(missing_ok=True)
                return None
            payload = path.read_bytes()
        except OSError:
            return None
        if not self._looks_like_image(payload, tile.extension):
            path.unlink(missing_ok=True)
            with self._lock:
                self._index.pop(tile.key, None)
            return None
        self._touch_index(tile, path, persist=False)
        return payload

    def _fetch_remote_tile(self, tile: TileRequest) -> tuple[bytes | None, str | None]:
        url = self._remote_url(tile)
        if not url:
            return None, None
        request = Request(
            url,
            headers={
                "User-Agent": "YKI/1.0 (PyQt6 Ground Control Station)",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        for _attempt in range(2):
            try:
                with self._fetch_gate:
                    with urlopen(request, timeout=7) as response:
                        status = getattr(response, "status", 200)
                        payload = response.read()
                        if 200 <= status < 300 and self._looks_like_image(payload, tile.extension):
                            return payload, response.headers.get_content_type()
            except (HTTPError, URLError, TimeoutError, OSError):
                time.sleep(0.12)
        return None, None

    def _remote_url(self, tile: TileRequest) -> str | None:
        if tile.provider == "carto":
            subdomains = "abcd"
            subdomain = subdomains[(tile.x + tile.y + tile.z) % len(subdomains)]
            return f"https://{subdomain}.basemaps.cartocdn.com/{tile.variant}/{tile.z}/{tile.x}/{tile.y}.png"
        if tile.provider == "esri" and tile.variant == "world-imagery":
            return (
                "https://server.arcgisonline.com/ArcGIS/rest/services/"
                f"World_Imagery/MapServer/tile/{tile.z}/{tile.y}/{tile.x}"
            )
        return None

    def _write_tile(self, tile: TileRequest, payload: bytes) -> None:
        path = self._tile_path(tile)
        with self._lock:
            is_new_tile = tile.key not in self._index
            ensure_directory(path.parent)
            temp_path = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
            temp_path.write_bytes(payload)
            temp_path.replace(path)
            self._touch_index(tile, path, persist=False)
            self._prune_if_needed_locked()
            self._request_persist_locked()
            count = len(self._index)
        if is_new_tile:
            self.cache_changed.publish(count)

    def _load_from_disk(self) -> None:
        with self._lock:
            self._load_from_disk_locked()

    def _load_from_disk_locked(self) -> None:
        self._index.clear()
        tiles_root = self._tiles_root
        if tiles_root.exists():
            for path in tiles_root.rglob("*"):
                tile = self._tile_from_path(path)
                if tile is None:
                    continue
                try:
                    if path.stat().st_size <= 0:
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                self._touch_index(tile, path, persist=False)
        self._prune_if_needed_locked()
        self._persist_index_locked()

    def _tile_from_path(self, path: Path) -> TileRequest | None:
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            return None
        try:
            provider, variant, z_text, x_text, filename = path.relative_to(self._tiles_root).parts
            y_text = Path(filename).stem
            extension = path.suffix.lower().lstrip(".")
            if extension == "jpeg":
                extension = "jpg"
            tile = TileRequest(provider, variant, int(z_text), int(x_text), int(y_text), extension)
        except (ValueError, TypeError):
            return None
        return tile if self._parse_tile_request(f"/tile/{tile.provider}/{tile.variant}/{tile.z}/{tile.x}/{tile.y}.{tile.extension}") else None

    def _touch_index(self, tile: TileRequest, path: Path, *, persist: bool) -> None:
        try:
            stat = path.stat()
            relative_path = str(path.relative_to(self._cache_path))
        except OSError:
            return
        with self._lock:
            self._index[tile.key] = {
                "key": tile.key,
                "provider": tile.provider,
                "variant": tile.variant,
                "z": tile.z,
                "x": tile.x,
                "y": tile.y,
                "extension": tile.extension,
                "path": relative_path,
                "size": stat.st_size,
                "updated_at": int(time.time()),
            }
            if persist:
                self._persist_index_locked()

    def _persist_index_locked(self) -> None:
        ensure_directory(self._cache_path)
        entries = sorted(self._index.values(), key=lambda item: item.get("updated_at", 0), reverse=True)
        temp_path = self.index_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        temp_path.replace(self.index_path)
        self._last_persist_at = time.monotonic()
        self._writes_since_persist = 0

    def _request_persist_locked(self) -> None:
        self._writes_since_persist += 1
        elapsed = time.monotonic() - self._last_persist_at
        if self._writes_since_persist >= 24 or elapsed >= 1.5:
            self._persist_index_locked()

    def _prune_if_needed_locked(self) -> None:
        limit_bytes = max(32, int(self._limit_mb)) * 1024 * 1024
        total = sum(int(entry.get("size", 0)) for entry in self._index.values())
        if total <= limit_bytes:
            return
        target = int(limit_bytes * 0.9)
        for entry in sorted(self._index.values(), key=lambda item: item.get("updated_at", 0)):
            path = self._cache_path / str(entry.get("path", ""))
            size = int(entry.get("size", 0))
            path.unlink(missing_ok=True)
            self._index.pop(str(entry.get("key")), None)
            total -= size
            if total <= target:
                break

    @staticmethod
    def _looks_like_image(payload: bytes | None, extension: str) -> bool:
        if not payload:
            return False
        if extension == "png":
            return payload.startswith(b"\x89PNG\r\n\x1a\n")
        return payload.startswith(b"\xff\xd8\xff")

    @staticmethod
    def _content_type(extension: str) -> str:
        if extension == "jpg":
            return "image/jpeg"
        return "image/png"
