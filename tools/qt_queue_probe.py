"""Measure the real Qt event bridge with a reproducible EventBus workload.

Run with the project Python. This is a synthetic subscription load, not a
hardware benchmark. Ordered mode preserves every event; latest mode coalesces
replaceable snapshots while preserving the final value.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from src.core.events import EventBus
from src.ui.adapters.services import _QtAdapter


def distribution(values):
    ordered = sorted(values)
    def percentile(p):
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * p))] if ordered else 0
    return {"count": len(ordered), "p50_ms": percentile(.5) * 1000,
            "p95_ms": percentile(.95) * 1000, "p99_ms": percentile(.99) * 1000,
            "max_ms": max(ordered, default=0) * 1000}


class AdapterProbe:
    """Opt-in live observer for one existing Qt service adapter.

    Attach from a debugger or a development session. Unkeyed callbacks retain
    their original order and arguments; keyed snapshots keep only their latest
    pending value. Sampling memory is bounded per adapter.
    """

    def __init__(self, adapter, sample_limit=10000):
        if sample_limit < 1:
            raise ValueError("sample_limit must be positive")
        self.adapter = adapter
        self.original = adapter._post
        self.samples = deque(maxlen=sample_limit)
        self.lock = threading.Lock()
        self.completed = 0

        def post(callback, *args, **kwargs):
            posted = time.perf_counter()
            depth = adapter._pending_events.qsize()
            name = getattr(callback, "__qualname__", type(callback).__name__)

            def measured(*values):
                begin = time.perf_counter()
                try:
                    return callback(*values)
                finally:
                    duration = time.perf_counter() - begin
                    with self.lock:
                        self.completed += 1
                        self.samples.append((name, begin - posted, duration, depth))

            self.original(measured, *args, **kwargs)

        self.post = post
        adapter._post = post

    def snapshot(self):
        with self.lock:
            samples, completed = tuple(self.samples), self.completed
        groups = defaultdict(list)
        for name, wait, duration, depth in samples:
            groups[name].append((wait, duration, depth))
        return {"completed": completed, "retained_samples": len(samples),
                "callbacks": {name: {
                    "delivery_wait": distribution([row[0] for row in rows]),
                    "callback_duration": distribution([row[1] for row in rows]),
                    "queue_depth_at_post_max": max(row[2] for row in rows),
                } for name, rows in groups.items()}}

    def close(self):
        if self.adapter._post is self.post:
            self.adapter._post = self.original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=float, default=1000)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--callback-ms", type=float, default=0)
    parser.add_argument("--stall-ms", type=float, default=0)
    parser.add_argument(
        "--policy",
        choices=("ordered", "latest"),
        default="ordered",
        help="ordered delivers every event; latest models replaceable state snapshots",
    )
    args = parser.parse_args()
    if args.rate <= 0 or args.seconds <= 0 or min(args.callback_ms, args.stall_ms) < 0:
        parser.error("rate/duration must be positive and delays nonnegative")
    app = QApplication(["qt-queue-probe"])
    adapter = _QtAdapter()
    stream = EventBus()
    waits, callbacks, heartbeat_lags, depths = [], [], [], []
    received_ids = []
    sent = [0]
    done = threading.Event()
    started = time.perf_counter()

    def receive(event):
        posted, event_id = event
        begin = time.perf_counter()
        waits.append(begin - posted)
        received_ids.append(event_id)
        if args.callback_ms:
            time.sleep(args.callback_ms / 1000)
        callbacks.append(time.perf_counter() - begin)

    adapter._subscribe(
        stream,
        receive,
        latest_by=(lambda _event: ("state",)) if args.policy == "latest" else None,
    )

    def produce():
        deadline = started + args.seconds
        next_event = started
        while next_event < deadline:
            delay = next_event - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            stream.publish((time.perf_counter(), sent[0]))
            sent[0] += 1
            next_event += 1 / args.rate
        done.set()

    last_tick = [time.perf_counter()]
    timer = QTimer()
    timer.setInterval(10)

    def tick():
        now = time.perf_counter()
        heartbeat_lags.append(max(0, now - last_tick[0] - .010))
        last_tick[0] = now
        depths.append(adapter._pending_events.qsize())
        if done.is_set() and received_ids and received_ids[-1] == sent[0] - 1:
            app.quit()
        elif now - started > args.seconds + 30:
            app.quit()

    timer.timeout.connect(tick)
    timer.start()
    if args.stall_ms:
        QTimer.singleShot(500, lambda: time.sleep(args.stall_ms / 1000))
    worker = threading.Thread(target=produce, name="queue-probe-producer")
    worker.start()
    app.exec()
    worker.join()
    elapsed = time.perf_counter() - started
    adapter.close()
    print(json.dumps({"workload": vars(args), "sent": sent[0], "received": len(waits),
                      "coalesced": sent[0] - len(waits),
                      "last_event_delivered": bool(received_ids and received_ids[-1] == sent[0] - 1),
                      "elapsed_s": elapsed, "drain_tail_s": max(0, elapsed - args.seconds),
                      "queue_depth_sampled_max": max(depths, default=0),
                      "delivery_wait": distribution(waits),
                      "callback_duration": distribution(callbacks),
                      "event_loop_lag": distribution(heartbeat_lags)}, indent=2))
    return 0 if (
        len(waits) == sent[0] if args.policy == "ordered"
        else bool(received_ids and received_ids[-1] == sent[0] - 1)
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
