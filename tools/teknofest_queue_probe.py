"""Measure the TEKNOFEST management executor using a loopback HTTP server.

The profile-stop hold and server delay are controlled synthetic delays. No
external TEKNOFEST service, credentials, vehicle telemetry, or database file is
used. Measurements separate executor queue wait, task work, HTTP round-trip and
loopback handler time; they are not a live network latency guarantee.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from extensions.siha_teknofest_server.models import CompetitionServerConfig
from extensions.siha_teknofest_server.runtime import CompetitionRuntime


def distribution(samples_s: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples_s)

    def percentile(percent: float) -> float:
        if not ordered:
            return 0.0
        return ordered[round((len(ordered) - 1) * percent)] * 1_000

    return {
        "count": len(ordered),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": max(ordered, default=0.0) * 1_000,
    }


class MeasuringExecutor:
    """Record FIFO executor wait and execution time without changing call order."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AGSProbe")
        self._lock = threading.Lock()
        self.samples: list[dict[str, float | str]] = []

    def submit(self, function, /, *args, **kwargs):
        queued_at = time.perf_counter()
        name = getattr(function, "__qualname__", type(function).__name__)

        def measured():
            started_at = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                finished_at = time.perf_counter()
                with self._lock:
                    self.samples.append(
                        {
                            "task": name,
                            "queue_wait_s": started_at - queued_at,
                            "execution_s": finished_at - started_at,
                        }
                    )

        return self._executor.submit(measured)

    def shutdown(self, **kwargs) -> None:
        self._executor.shutdown(**kwargs)


class LoopbackHandler(BaseHTTPRequestHandler):
    response_delay_s = 0.0
    durations: list[float] = []
    durations_lock = threading.Lock()

    def _send_json(self, payload: bytes) -> None:
        started_at = time.perf_counter()
        if self.response_delay_s:
            time.sleep(self.response_delay_s)
        elapsed = time.perf_counter() - started_at
        with self.durations_lock:
            self.durations.append(elapsed)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/api/sunucusaati":
            self._send_json(b'{"gun":24,"saat":12,"dakika":0,"saniye":0}')
        else:
            self._send_json(b'{"accepted":true}')

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/api/giris":
            payload = b'{"takim_numarasi":1}'
        else:
            payload = b'{"accepted":true}'
        self._send_json(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


class MemoryDatabase:
    def __init__(self) -> None:
        self.durations_s: list[float] = []

    def record(self, *_args) -> None:
        self.durations_s.append(float(_args[-1]))


class Settings:
    def __init__(self, servers: list[dict[str, object]]) -> None:
        self.servers = servers

    def extension_config(self, _identifier: str) -> dict[str, object]:
        return {"servers": self.servers}

    def update_extension(self, _identifier: str, *, config: dict[str, object]) -> None:
        self.servers = config["servers"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=20)
    parser.add_argument("--management-stall-ms", type=float, default=20.0)
    parser.add_argument("--profile-stop-delay-ms", type=float, default=25.0)
    parser.add_argument("--server-delay-ms", type=float, default=15.0)
    args = parser.parse_args()
    if args.events <= 0 or min(
        args.management_stall_ms, args.profile_stop_delay_ms, args.server_delay_ms
    ) < 0:
        parser.error("event count must be positive and delays nonnegative")

    LoopbackHandler.response_delay_s = args.server_delay_ms / 1_000
    LoopbackHandler.durations = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    identifier = "loopback-probe"
    initial_config = CompetitionServerConfig(identifier=identifier).to_dict(
        include_password=True
    )
    config = CompetitionServerConfig(
        identifier=identifier,
        name="Loopback timing probe",
        base_url=f"http://127.0.0.1:{server.server_port}",
        username="probe",
        password="not-a-real-secret",
        timeout_s=3.0,
        auto_connect=False,
    ).to_dict(include_password=True)

    class PreviousSession:
        config = {"timeout_s": 3.0}
        running = True

        def stop(self, *, timeout_s=None) -> bool:
            if timeout_s == 0:
                return False
            time.sleep(args.profile_stop_delay_ms / 1_000)
            self.running = False
            return True

    worker = MeasuringExecutor()
    runtime = object.__new__(CompetitionRuntime)
    runtime._lifecycle_lock = threading.RLock()
    runtime._closed = False
    runtime.settings = Settings([initial_config])
    runtime.telemetry = SimpleNamespace(
        current=SimpleNamespace(has_valid_position=False)
    )
    runtime.database = MemoryDatabase()
    runtime.logger = SimpleNamespace(event=lambda *_args, **_kwargs: None)
    runtime.clock_references = None
    runtime._clock_reference_ids = set()
    runtime.sessions = {identifier: PreviousSession()}
    runtime._profile_generations = {identifier: 1}
    runtime.worker = worker

    entered = threading.Event()
    release = threading.Event()

    def hold_management_queue() -> None:
        entered.set()
        release.wait()
        time.sleep(args.management_stall_ms / 1_000)

    try:
        configure_started = time.perf_counter()
        configure = runtime.configure([config])
        configure.result(timeout=30.0)
        configure_wall_s = time.perf_counter() - configure_started

        runtime.connect(identifier).result(timeout=5.0)
        session = runtime.sessions[identifier]
        login_deadline = time.monotonic() + 5.0
        while not session.connected and time.monotonic() < login_deadline:
            if not session.running:
                break
            time.sleep(0.01)
        if not session.connected:
            raise RuntimeError(
                f"loopback server login did not complete: {session.error or 'no response'}"
            )

        blocker = worker.submit(hold_management_queue)
        if not entered.wait(2.0):
            raise TimeoutError("management queue probe worker did not start")
        requests = []
        for index in range(args.events):
            event = SimpleNamespace(to_payload=lambda index=index: {"probe": index})
            if index % 2:
                requests.append(runtime.submit_kamikaze_event(identifier, event))
            else:
                requests.append(runtime.submit_lock_event(identifier, event))
        release.set()
        responses = [future.result(timeout=30.0) for future in requests]
        blocker.result(timeout=2.0)
    finally:
        release.set()
        runtime.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)

    grouped = defaultdict(list)
    for sample in worker.samples:
        grouped[sample["task"]].append(sample)
    queue_wait = [float(sample["queue_wait_s"]) for sample in worker.samples]
    request_times = runtime.database.durations_s
    server_times = list(LoopbackHandler.durations)
    configure_samples = [
        float(sample["execution_s"])
        for sample in grouped["CompetitionRuntime.configure.<locals>.apply"]
    ]
    print(
        json.dumps(
            {
                "workload": vars(args),
                "target": "127.0.0.1 loopback only",
                "synthetic_delays": {
                    "management_queue_hold_ms": args.management_stall_ms,
                    "old_profile_stop_ms": args.profile_stop_delay_ms,
                    "server_handler_ms": args.server_delay_ms,
                },
                "submitted_events": args.events,
                "successful_responses": len(responses),
                "configure_submission_to_completion_ms": configure_wall_s * 1_000,
                "management_queue_wait": distribution(queue_wait),
                "profile_configuration_execution": distribution(configure_samples),
                "http_request_round_trip": distribution(request_times),
                "loopback_handler_time": distribution(server_times),
                "tasks": {
                    name: {
                        "count": len(samples),
                        "queue_wait": distribution(
                            [float(sample["queue_wait_s"]) for sample in samples]
                        ),
                        "execution": distribution(
                            [float(sample["execution_s"]) for sample in samples]
                        ),
                    }
                    for name, samples in grouped.items()
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
