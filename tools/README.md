# Runtime measurements

Run the queue probe with the project's virtual environment:

```sh
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2 --callback-ms 2
.venv/bin/python tools/qt_queue_probe.py --rate 1000 --seconds 2 --stall-ms 200
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
budget. Move confirmed expensive work off the GUI thread before considering
event-specific coalescing; command results must retain their delivery semantics.

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
