"""Measure tile-cache indexing and hits using real temporary tile files.

This is a developer probe, not a unit test. It creates a temporary, file-backed
cache and removes it when the process exits. It never contacts a tile provider.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from extensions.map_autonomy.services.tile_cache import TileCacheServer, TileRequest


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)


def distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}

    def percentile(percent: float) -> float:
        index = round((len(ordered) - 1) * percent)
        return ordered[index]

    return {
        "count": len(ordered),
        "p50_ms": round(percentile(0.50) * 1000, 3),
        "p95_ms": round(percentile(0.95) * 1000, 3),
        "p99_ms": round(percentile(0.99) * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def make_tile(index: int, zoom: int) -> TileRequest:
    width = 1 << zoom
    return TileRequest(
        "esri",
        "world-imagery",
        zoom,
        index // width,
        index % width,
        "png",
    )


def probe_count(
    count: int,
    *,
    hit_iterations: int,
    concurrency: int,
    measure_prune: bool,
) -> dict:
    zoom = max(1, (count - 1).bit_length() // 2 + 1)
    with tempfile.TemporaryDirectory(prefix="ags-tile-cache-probe-") as directory:
        root = Path(directory)
        for index in range(count):
            tile = make_tile(index, zoom)
            path = root / "tiles" / tile.provider / tile.variant / str(zoom)
            path = path / str(tile.x) / f"{tile.y}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PNG_1X1)

        started = time.perf_counter()
        server = TileCacheServer(
            cache_path=root,
            enabled=True,
            limit_mb=1024,
        )
        startup_s = time.perf_counter() - started
        try:
            indexed = server.count_tiles()
            tile_count_bytes = server._total_bytes
            hit_tile = make_tile(count // 2, zoom)
            hit_path = f"/tile/esri/world-imagery/{zoom}/{hit_tile.x}/{hit_tile.y}.png"
            hit_latencies: list[float] = []
            for _ in range(hit_iterations):
                started = time.perf_counter()
                result = server.handle_request(hit_path)
                hit_latencies.append(time.perf_counter() - started)
                if result.status != 200 or result.cache_state != "hit":
                    raise RuntimeError("expected every warmed tile request to be a cache hit")

            parallel_paths = [
                f"/tile/esri/world-imagery/{zoom}/{make_tile(index, zoom).x}/"
                f"{make_tile(index, zoom).y}.png"
                for index in range(min(concurrency, count))
            ]
            concurrent_latencies: list[float] = []

            def read_path(path: str) -> float:
                started_at = time.perf_counter()
                result = server.handle_request(path)
                elapsed = time.perf_counter() - started_at
                if result.status != 200 or result.cache_state != "hit":
                    raise RuntimeError("concurrent cache request was not a hit")
                return elapsed

            rounds = max(1, hit_iterations // max(1, len(parallel_paths)))
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                for _ in range(rounds):
                    concurrent_latencies.extend(executor.map(read_path, parallel_paths))

            index_write_started = time.perf_counter()
            with server._lock:
                server._persist_index_locked()
            index_write_s = time.perf_counter() - index_write_started

            server.stop()
            checkpoint_started = time.perf_counter()
            server = TileCacheServer(
                cache_path=root,
                enabled=True,
                limit_mb=1024,
            )
            checkpoint_load_s = time.perf_counter() - checkpoint_started
            if server.count_tiles() != count:
                raise RuntimeError("checkpoint reopen did not restore every tile")

            prune_s = None
            if measure_prune and tile_count_bytes > 1024 * 1024:
                prune_started = time.perf_counter()
                with server._lock:
                    server._limit_mb = 1
                    server._prune_if_needed_locked()
                    server._persist_index_locked()
                prune_s = time.perf_counter() - prune_started

            return {
                "tile_files": count,
                "indexed_tiles": indexed,
                "tile_payload_bytes": tile_count_bytes,
                "startup_rebuild_ms": round(startup_s * 1000, 3),
                "checkpoint_load_ms": round(checkpoint_load_s * 1000, 3),
                "index_persist_ms": round(index_write_s * 1000, 3),
                "warm_cache_hit": distribution(hit_latencies),
                "concurrent_cache_hits": {
                    "workers": concurrency,
                    **distribution(concurrent_latencies),
                },
                "prune_and_persist_ms": (
                    None if prune_s is None else round(prune_s * 1000, 3)
                ),
            }
        finally:
            server.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", default="10000,50000,100000")
    parser.add_argument("--hit-iterations", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    try:
        counts = [int(value) for value in args.counts.split(",")]
    except ValueError:
        parser.error("counts must be comma-separated positive integers")
    if not counts or min(counts) <= 0:
        parser.error("counts must be comma-separated positive integers")
    if args.hit_iterations <= 0 or args.concurrency <= 0:
        parser.error("hit iterations and concurrency must be positive")

    results = [
        probe_count(
            count,
            hit_iterations=args.hit_iterations,
            concurrency=args.concurrency,
            measure_prune=count == max(counts),
        )
        for count in counts
    ]
    print(
        json.dumps(
            {
                "measurement": "temporary file-backed tile-cache rebuild and checkpoint load",
                "provider_access": "none; all image files are generated locally",
                "tile_fixture": (
                    f"same {len(PNG_1X1)}-byte valid 1x1 PNG repeated for each tile; "
                    "file count and tree are real, image-size distribution is synthetic"
                ),
                "filesystem_state": "warm OS page cache after index scan",
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
