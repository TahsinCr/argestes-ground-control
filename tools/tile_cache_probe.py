"""Measure warm tile-cache hits against a direct file read.

This probe uses one local, page-cache-warm image and a synthetic in-memory
index at several sizes. It does not fetch map data or create thousands of
files; it isolates the lookup, metadata and lock work added by a cache hit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from extensions.map_autonomy.services.tile_cache import TileCacheServer, TileRequest


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def distribution(samples_ns: list[int]) -> dict[str, float | int]:
    ordered = sorted(samples_ns)

    def percentile(percent: float) -> float:
        if not ordered:
            return 0.0
        return ordered[round((len(ordered) - 1) * percent)] / 1_000

    return {
        "count": len(ordered),
        "p50_us": percentile(0.50),
        "p95_us": percentile(0.95),
        "p99_us": percentile(0.99),
        "max_us": max(ordered, default=0) / 1_000,
    }


def measure(operation, iterations: int) -> list[int]:
    for _ in range(min(100, iterations)):
        operation()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=2_000)
    parser.add_argument("--sizes", default="10000,50000,100000")
    parser.add_argument("--payload-kib", type=int, default=32)
    args = parser.parse_args()
    try:
        sizes = [int(value) for value in args.sizes.split(",")]
    except ValueError:
        parser.error("--sizes must be a comma-separated list of positive integers")
    if args.iterations <= 0 or args.payload_kib <= 0 or not sizes or min(sizes) <= 0:
        parser.error("iterations, payload size and index sizes must be positive")

    payload = PNG_SIGNATURE + bytes(args.payload_kib * 1024 - len(PNG_SIGNATURE))
    tile = TileRequest("probe", "default", 12, 2200, 1400, "png")
    results = []
    with tempfile.TemporaryDirectory(prefix="ags-tile-probe-") as directory:
        cache = TileCacheServer(cache_path=directory, enabled=True, limit_mb=256)
        try:
            path = cache._tile_path(tile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            for size in sizes:
                synthetic = {
                    f"synthetic:{index}": {"size": len(payload), "updated_at": 1}
                    for index in range(size - 1)
                }
                synthetic[tile.key] = {
                    "key": tile.key,
                    "provider": tile.provider,
                    "variant": tile.variant,
                    "z": tile.z,
                    "x": tile.x,
                    "y": tile.y,
                    "extension": tile.extension,
                    "path": str(path.relative_to(cache._cache_path)),
                    "size": len(payload),
                    "updated_at": 1,
                }
                cache._index = synthetic
                cache._total_bytes = size * len(payload)

                def direct_read() -> bytes:
                    return path.read_bytes()

                raw_samples = measure(direct_read, args.iterations)
                cache_samples = measure(
                    lambda: cache._read_cached_tile(tile), args.iterations
                )
                results.append(
                    {
                        "synthetic_index_entries": size,
                        "direct_read": distribution(raw_samples),
                        "cache_hit": distribution(cache_samples),
                    }
                )
        finally:
            cache.stop()

    print(
        json.dumps(
            {
                "measurement": "warm local-file read versus TileCacheServer hit",
                "payload_bytes": len(payload),
                "iterations_per_case": args.iterations,
                "filesystem_state": "one page-cache-warm image; all other index entries are synthetic",
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
