from __future__ import annotations

import asyncio
import concurrent.futures
import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from src.core.events import EventBus
from extensions.map_autonomy.api.tile_cache import TileCacheService
from src.services.async_runtime import AsyncRuntime
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


class TileCacheServer(TileCacheService):
    """Cache-first local tile server used by Leaflet.

    Leaflet only talks to this local server when map cache is enabled. The
    server is the single owner of tile persistence: check disk first, fetch the
    missing tile once, write it atomically, then answer the browser.
    """

    MAX_TILE_BYTES = 12 * 1024 * 1024

    def __init__(
        self,
        *,
        cache_path: str | Path,
        enabled: bool,
        limit_mb: int,
        parent: object | None = None,
        async_runtime: AsyncRuntime | None = None,
    ) -> None:
        del parent
        self._cache_changed = EventBus[int]()
        self._cache_path = Path(cache_path)
        self._enabled = enabled
        self._limit_mb = limit_mb
        self._async_runtime = async_runtime or AsyncRuntime()
        self._owns_async_runtime = async_runtime is None
        self._server: asyncio.AbstractServer | None = None
        self._server_address: tuple[str, int] | None = None
        self._request_gate: asyncio.Semaphore | None = None
        self._clients: set[asyncio.Task] = set()
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = {}
        self._total_bytes = 0
        self._generation = 0
        self._inflight: dict[str, threading.Event] = {}
        self._fetch_gate = threading.BoundedSemaphore(6)
        self._last_persist_at = 0.0
        self._writes_since_persist = 0
        self._load_from_disk()

    @property
    def cache_changed(self) -> EventBus[int]:
        return self._cache_changed

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def base_url(self) -> str:
        if not self._server_address:
            return ""
        host, port = self._server_address
        return f"http://{host}:{port}"

    @property
    def index_path(self) -> Path:
        return self._cache_path / "cache-index.json"

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def configure(
        self, *, cache_path: str | Path, enabled: bool, limit_mb: int
    ) -> None:
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
        future = self._async_runtime.submit(self._start_server())
        try:
            future.result(timeout=2.0)
        except (OSError, concurrent.futures.TimeoutError):
            # A hardened runtime may forbid local sockets. The map falls back
            # to provider URLs when base_url is empty.
            future.cancel()
            with self._lock:
                self._server = None
                self._server_address = None

    def stop(self) -> None:
        with self._lock:
            server = self._server
        if server is None:
            if self._owns_async_runtime:
                self._async_runtime.stop()
            return
        future = self._async_runtime.submit(self._stop_server(server))
        try:
            future.result(timeout=2.0)
        except concurrent.futures.TimeoutError:
            future.cancel()
        with self._lock:
            self._persist_index_locked()
            if self._server is server:
                self._server = None
            self._server_address = None
        if self._owns_async_runtime:
            self._async_runtime.stop()

    async def _start_server(self) -> None:
        self._request_gate = asyncio.Semaphore(8)
        server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
            limit=16 * 1024,
        )
        socket = next(iter(server.sockets or ()), None)
        if socket is None:
            server.close()
            await server.wait_closed()
            raise OSError("Local tile cache socket could not be opened")
        host, port = socket.getsockname()[:2]
        with self._lock:
            self._server = server
            self._server_address = (str(host), int(port))
        self.cache_changed.publish(self.count_tiles())

    async def _stop_server(self, server: asyncio.AbstractServer) -> None:
        server.close()
        await server.wait_closed()
        clients = tuple(self._clients)
        for task in clients:
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2.0)
            request_line = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
            method, path, _version = request_line.split(" ", 2)
            if method != "GET":
                result = TileResult(405, "text/plain", b"", "method-not-allowed")
            else:
                gate = self._request_gate
                if gate is None:
                    return
                async with gate:
                    result = await asyncio.to_thread(self.handle_request, path)
            reason = {
                200: "OK",
                302: "Found",
                400: "Bad Request",
                404: "Not Found",
                405: "Method Not Allowed",
                503: "Service Unavailable",
                504: "Gateway Timeout",
            }.get(result.status, "OK")
            headers = [
                f"HTTP/1.1 {result.status} {reason}",
                f"Content-Type: {result.content_type}",
                f"Content-Length: {len(result.payload)}",
                "Access-Control-Allow-Origin: *",
                "Cache-Control: no-store, max-age=0",
                f"X-YKI-Tile-Cache: {result.cache_state}",
                "Connection: close",
            ]
            if result.redirect_url:
                headers.append(f"Location: {result.redirect_url}")
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
            writer.write(result.payload)
            await writer.drain()
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            asyncio.TimeoutError,
            ConnectionError,
            ValueError,
        ):
            pass
        finally:
            if task is not None:
                self._clients.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    def restart(self) -> None:
        self.stop()
        self.start()

    def clear(self) -> None:
        with self._lock:
            # Any remote fetch already in progress belongs to the cache state
            # that is being deleted; it may still serve its caller, but must
            # not repopulate the cache after this point.
            self._generation += 1
            shutil.rmtree(self._tiles_root, ignore_errors=True)
            self._index.clear()
            self._total_bytes = 0
            self.index_path.unlink(missing_ok=True)
        self.cache_changed.publish(0)

    def count_tiles(self) -> int:
        with self._lock:
            return len(self._index)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                self._index.values(),
                key=lambda item: (
                    item.get("provider", ""),
                    item.get("variant", ""),
                    item.get("z", 0),
                    item.get("x", 0),
                    item.get("y", 0),
                ),
            )

    def handle_request(self, raw_path: str) -> TileResult:
        with self._lock:
            generation = self._generation
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
                    return TileResult(
                        200,
                        self._content_type(tile.extension),
                        cached,
                        "hit-after-wait",
                    )
            else:
                owns_fetch = True

        try:
            payload, content_type = self._fetch_remote_tile(tile)
            if payload:
                if self._enabled:
                    self._write_tile(tile, payload, generation=generation)
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
            return TileResult(
                302,
                "text/plain; charset=utf-8",
                b"",
                "miss-redirect-fallback",
                remote_url,
            )
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
        parts = [
            unquote(part)
            for part in urlparse(raw_path).path.strip("/").split("/")
            if part
        ]
        if len(parts) != 6 or parts[0] != "tile":
            return None
        _, provider, variant, z_text, x_text, filename = parts
        if (
            not self._safe_component(provider)
            or not self._safe_component(variant)
            or "." not in filename
        ):
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
        return TileRequest(
            provider=provider, variant=variant, z=z, x=x, y=y, extension=extension
        )

    @staticmethod
    def _safe_component(value: str) -> bool:
        return bool(value) and all(
            char.isalnum() or char in {"-", "_"} for char in value
        )

    def _tile_path(self, tile: TileRequest) -> Path:
        return (
            self._tiles_root
            / tile.provider
            / tile.variant
            / str(tile.z)
            / str(tile.x)
            / f"{tile.y}.{tile.extension}"
        )

    def _read_cached_tile(self, tile: TileRequest) -> bytes | None:
        path = self._tile_path(tile)
        try:
            payload = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            self._remove_index_entry(tile)
            return None
        except OSError:
            return None
        if not payload:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._remove_index_entry(tile)
            return None
        if not self._looks_like_image(payload, tile.extension):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._remove_index_entry(tile)
            return None
        self._touch_index(tile, path, persist=False, size=len(payload))
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
                        content_length = response.headers.get("Content-Length")
                        if content_length is not None:
                            try:
                                if int(content_length) > self.MAX_TILE_BYTES:
                                    return None, None
                            except ValueError:
                                pass
                        payload = response.read(self.MAX_TILE_BYTES + 1)
                        if len(payload) > self.MAX_TILE_BYTES:
                            return None, None
                        if 200 <= status < 300 and self._looks_like_image(
                            payload, tile.extension
                        ):
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

    def _write_tile(
        self,
        tile: TileRequest,
        payload: bytes,
        *,
        generation: int,
    ) -> None:
        path = self._tile_path(tile)
        with self._lock:
            if generation != self._generation:
                return
            is_new_tile = tile.key not in self._index
            ensure_directory(path.parent)
            temp_path = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
            temp_path.write_bytes(payload)
            temp_path.replace(path)
            self._touch_index(tile, path, persist=False, size=len(payload))
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
        self._total_bytes = 0
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
            provider, variant, z_text, x_text, filename = path.relative_to(
                self._tiles_root
            ).parts
            y_text = Path(filename).stem
            extension = path.suffix.lower().lstrip(".")
            if extension == "jpeg":
                extension = "jpg"
            tile = TileRequest(
                provider, variant, int(z_text), int(x_text), int(y_text), extension
            )
        except (ValueError, TypeError):
            return None
        return (
            tile
            if self._parse_tile_request(
                f"/tile/{tile.provider}/{tile.variant}/{tile.z}/{tile.x}/{tile.y}.{tile.extension}"
            )
            else None
        )

    def _touch_index(
        self,
        tile: TileRequest,
        path: Path,
        *,
        persist: bool,
        size: int | None = None,
    ) -> None:
        updated_at = int(time.time())
        try:
            if size is None:
                size = path.stat().st_size
        except OSError:
            return
        with self._lock:
            previous = self._index.get(tile.key)
            if (
                not persist
                and previous is not None
                and int(previous.get("size", -1)) == size
            ):
                if int(previous.get("updated_at", -1)) != updated_at:
                    previous["updated_at"] = updated_at
                return
        try:
            relative_path = str(path.relative_to(self._cache_path))
        except (OSError, ValueError):
            return
        with self._lock:
            previous = self._index.get(tile.key)
            if previous is not None:
                self._total_bytes -= int(previous.get("size", 0))
            self._index[tile.key] = {
                "key": tile.key,
                "provider": tile.provider,
                "variant": tile.variant,
                "z": tile.z,
                "x": tile.x,
                "y": tile.y,
                "extension": tile.extension,
                "path": relative_path,
                "size": size,
                "updated_at": updated_at,
            }
            self._total_bytes += size
            if persist:
                self._persist_index_locked()

    def _remove_index_entry(self, tile: TileRequest) -> None:
        with self._lock:
            removed = self._index.pop(tile.key, None)
            if removed is None:
                return
            self._total_bytes = max(
                0,
                self._total_bytes - int(removed.get("size", 0)),
            )
            self._request_persist_locked()

    def _persist_index_locked(self) -> None:
        ensure_directory(self._cache_path)
        entries = sorted(
            self._index.values(),
            key=lambda item: item.get("updated_at", 0),
            reverse=True,
        )
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
        total = self._total_bytes
        if total <= limit_bytes:
            return
        target = int(limit_bytes * 0.9)
        for entry in sorted(
            self._index.values(), key=lambda item: item.get("updated_at", 0)
        ):
            path = self._cache_path / str(entry.get("path", ""))
            size = int(entry.get("size", 0))
            path.unlink(missing_ok=True)
            self._index.pop(str(entry.get("key")), None)
            total -= size
            if total <= target:
                break
        self._total_bytes = max(0, total)

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
