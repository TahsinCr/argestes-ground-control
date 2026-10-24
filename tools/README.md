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
