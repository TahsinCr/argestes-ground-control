# Runtime measurements

Run the queue probe with the project's virtual environment:

```sh
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2 --callback-ms 2
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2 --stall-ms 200
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2 --callback-ms 2 --policy latest
```

The tool subscribes through the application's actual `_QtAdapter` to an
EventBus producer. Each event carries a monotonic timestamp. JSON output reports
delivery wait and callback duration separately, Qt timer delay, queue depth
sampled every 10 ms, delivered counts and the time needed to drain the tail.
The sampled queue maximum is a lower bound; a short peak may occur between timer
samples. Callback time includes scheduler delays. These are synthetic service
events, not measurements of vehicle or camera latency.

On 24 October 2026, 1,000 events/s with a trivial callback produced a delivery
P95 of 0.156 ms. Adding a 200 ms GUI-thread stall raised delivery P95 to 99.3 ms.
Adding 2 ms of work to every callback overloaded the consumer: queue depth
exceeded 1,000 and delivery P95 exceeded two seconds. No events were lost.

With count-only batches, the slow-callback workload delayed the Qt timer by
127 ms at P95. A four-millisecond draining budget reduced that to 3.21 ms in
the same workload. Tail draining remained around 2.3 seconds: yielding improves
input/paint responsiveness but cannot make a consumer process work faster than
its callbacks permit. A single long callback also cannot be interrupted by a
budget. The adapter therefore coalesces only explicitly replaceable settings
and per-channel connection snapshots; command and operation results stay FIFO.
The `latest` probe mode measures the bounded-queue effect for state snapshots
and verifies that the final value is delivered.

On 24 October 2026, a 2 s, 1,000 events/s workload with 2 ms callback work
delivered all 2,001 ordered events but peaked at 1,075 pending items; delivery
P95 was 2.26 s and the tail took 2.38 s. With `--policy latest`, the queue
remained at one item, 1,074 intermediate snapshots were coalesced, and the
final snapshot arrived; delivery P95 was 0.97 ms and the drain tail was 4.5 ms.
These numbers describe the probe machine and synthetic consumer, not a live
flight workload.

Repeat runs on the target machine and compare counts and distributions together.
Redirect JSON output to a local file when collecting longer runs.

## TEKNOFEST management-queue probe

Measure the current `CompetitionRuntime` management executor against a local
HTTP endpoint only:

```sh
.venv/bin/python tools/teknofest_queue_probe.py --events 20
.venv/bin/python tools/teknofest_queue_probe.py --events 20 --management-stall-ms 20 --profile-stop-delay-ms 25 --server-delay-ms 15
```

The probe records enqueue-to-start wait and task execution separately, uses the
real runtime configuration and request path, and reports the client HTTP round
trip beside loopback handler time. Its endpoint returns a valid login and server
clock, and the probe waits for authentication before queueing management events.
Queue and profile-stop holds are explicitly synthetic; the tool never contacts
a real TEKNOFEST host or vehicle. It is useful for identifying which portion of
a controlled scenario occupies the serial executor, not for predicting a radio
or competition-server SLA.

On 24 October 2026, the authenticated 20-event run with a 20 ms queue hold,
25 ms old-profile stop and 15 ms loopback response delay completed all events.
Profile replacement executed in 30.0 ms; the client request round trip was
16.0 ms at P50. Across all management-worker tasks, queue wait was 149.4 ms at
P50 / 310.2 ms at P95; for event submissions alone it was 181.9 ms at P50 /
310.2 ms at P95. The wait accumulates because the same worker deliberately
processes each delayed request in order. This synthetic result does not justify
a second worker pool; real server and radio timings still need to be measured
before changing event ordering.

## Tile-cache hit probe

Compare the existing file-backed cache-hit path with a direct read using a
page-cache-warm 32 KiB image and synthetic indexes of increasing size:

```sh
.venv/bin/python tools/tile_cache_probe.py
.venv/bin/python tools/tile_cache_probe.py --iterations 5000 --sizes 10000,50000,100000
```

Only one image file is created; the remaining index entries are in-memory test
data. The reported difference estimates the Python metadata, validation and
locking work around a local read. It excludes network fetches, cold storage,
real-world tile-size distribution and concurrent map requests.

On 24 October 2026, the 100,000-entry case measured a direct-read P50 of 3.3 µs
and a cache-hit P50 of 10.9 µs (P95 3.4 µs and 11.2 µs respectively). Avoiding
repeated metadata calls and rebuilding an unchanged index entry brought the
cache-hit median down from about 22.3 µs to roughly 10.8 µs across repeated runs.
The cache remains file-backed, and its LRU timestamp still advances at the
existing one-second resolution.

## Tile-cache file-tree probe

Measure startup index rebuild, clean checkpoint reopen, JSON persistence, warm
cache hits, eight-way concurrent hits, and pruning using real temporary files:

```sh
.venv/bin/python tools/tile_cache_storage_probe.py
.venv/bin/python tools/tile_cache_storage_probe.py --counts 10000,50000,100000 --hit-iterations 500
```

The probe does not contact a tile provider. It writes one valid 68-byte 1x1 PNG
per tile, so the directory tree and file count are real but payload sizes do not
represent a normal map. On this machine `/tmp` is `tmpfs`; the measurements below
are a warm-cache indexing baseline, not SSD/HDD performance.

On 24 October 2026, the 10k / 50k / 100k cold-index rebuilds took 498 ms /
2.48 s / 5.00 s; reopening the clean compact checkpoint took 47 ms / 275 ms /
513 ms. Persisting the 100k-entry checkpoint took 88 ms. Warm-hit P50 was
17–18 µs, and eight concurrent cache hits had a P95 of 0.45–0.55 ms. Pruning a
100k-file cache to a 1 MiB limit and persisting the remainder took 64 ms on
tmpfs. A dirty, missing or invalid checkpoint still triggers a full scan; this
keeps interrupted cache writes recoverable. These measurements use synthetic
68-byte PNGs and a warm `/tmp` tmpfs page cache, not the target storage device.

For a running application, import `AdapterProbe` from `tools.qt_queue_probe` in
a development/debugger session and construct it with an existing Qt service
adapter: `probe = AdapterProbe(qt_service)`. Existing subscriptions then pass
through the observer. `probe.snapshot()` separates wait and execution time by
callback name; `probe.close()` restores the original dispatch method. It keeps
at most 10,000 completed samples by default. Install/remove the observer from
the GUI thread. This adds measurement overhead while attached and observes only
callbacks dispatched through that adapter's `_post` method.

## Local camera capture probe

Use this only when it is safe to take the selected camera device for a short
test. It runs the camera extension's `GStreamerVideoService` USB path, does not
save or display frames, and accepts black or covered frames.

```sh
.venv/bin/python tools/camera_capture_probe.py --device /dev/video0 --seconds 10
```

The JSON report includes delivered frame rate and dimensions, RGBA bytes copied
by the capture path, per-frame callback duration, frame interval, process CPU
time, and peak resident memory. This is a single-camera local measurement; it
does not replace three-camera, encoder, or SITL tests.

On 24 October 2026, a covered `/dev/video0` delivered 169 frames over 10 s at
640×480 (16.83 FPS). Capture callback time was 0.78 ms at P95, copied frame data
averaged 19.80 MiB/s, and process CPU was 4.0%. RSS was 27.7 MiB before capture
and peaked at 49.8 MiB during the sampled run; the process high-water mark was
591.5 MiB. Because covering the lens can make camera auto-exposure lengthen
frame intervals, this run measures copy/callback cost rather than the device's
normal maximum frame rate. No recording or encoder was active.
