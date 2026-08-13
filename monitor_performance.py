#!/usr/bin/env python3
"""Run YKI and report CPU plus proportional/resident memory on Linux."""

from __future__ import annotations

import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


PROJECT_ROOT = Path(__file__).resolve().parent
PROC_ROOT = Path("/proc")
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProcessUsage:
    pid: int
    role: str
    cpu_seconds: float
    pss_bytes: int
    rss_bytes: int


def _stat_fields(pid: int) -> list[str]:
    raw = (PROC_ROOT / str(pid) / "stat").read_text(encoding="utf-8")
    closing_parenthesis = raw.rfind(")")
    if closing_parenthesis < 0:
        raise ValueError("invalid proc stat record")
    return raw[closing_parenthesis + 2 :].split()


def _process_table() -> dict[int, int]:
    parents: dict[int, int] = {}
    for entry in PROC_ROOT.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = _stat_fields(int(entry.name))
            parents[int(entry.name)] = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
    return parents


def _process_tree(root_pid: int) -> set[int]:
    parents = _process_table()
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in parents.items():
            if parent_pid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return selected


def _memory_bytes(pid: int) -> tuple[int, int]:
    values: dict[str, int] = {}
    rollup = PROC_ROOT / str(pid) / "smaps_rollup"
    try:
        lines = rollup.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = (PROC_ROOT / str(pid) / "status").read_text(encoding="utf-8").splitlines()
    for line in lines:
        key, separator, remainder = line.partition(":")
        if separator and key in {"Pss", "Rss", "VmRSS"}:
            values[key] = int(remainder.split()[0]) * 1024
    rss = values.get("Rss", values.get("VmRSS", 0))
    return values.get("Pss", rss), rss


def _role(pid: int, root_pid: int) -> str:
    try:
        command = (PROC_ROOT / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return "unknown"
    if pid == root_pid:
        return "yki"
    lowered = command.lower()
    marker = "--type="
    if marker in lowered:
        process_type = lowered.split(marker, 1)[1].split()[0]
        return f"web:{process_type}"
    if "qtwebengineprocess" in lowered:
        return "web"
    return Path(command.split()[0]).name if command.split() else "child"


def _sample(root_pid: int) -> tuple[ProcessUsage, ...]:
    records: list[ProcessUsage] = []
    for pid in _process_tree(root_pid):
        try:
            stat = _stat_fields(pid)
            pss, rss = _memory_bytes(pid)
            records.append(
                ProcessUsage(
                    pid=pid,
                    role=_role(pid, root_pid),
                    cpu_seconds=(int(stat[11]) + int(stat[12])) / CLOCK_TICKS,
                    pss_bytes=pss,
                    rss_bytes=rss,
                )
            )
        except (OSError, IndexError, ValueError):
            continue
    return tuple(records)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start main.py and monitor YKI.")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--details-every",
        type=int,
        default=10,
        help="Print process memory details every N samples; 0 disables them.",
    )
    parser.add_argument("--csv", type=Path, help="Write samples to a CSV file.")
    parser.add_argument("main_args", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.interval < 0.1:
        parser.error("--interval must be at least 0.1 seconds")
    if arguments.details_every < 0:
        parser.error("--details-every cannot be negative")
    if arguments.main_args[:1] == ["--"]:
        arguments.main_args = arguments.main_args[1:]
    return arguments


def _open_csv(path: Path | None) -> tuple[TextIO | None, csv.writer | None]:
    if path is None:
        return None, None
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    handle = resolved.open("w", encoding="utf-8", newline="")
    writer = csv.writer(handle)
    writer.writerow(("elapsed_s", "cpu_percent", "pss_mib", "rss_mib", "processes"))
    return handle, writer


def main() -> int:
    if not PROC_ROOT.is_dir():
        print("Error: this monitor requires Linux procfs (/proc).", file=sys.stderr)
        return 2
    arguments = _arguments()
    csv_handle, csv_writer = _open_csv(arguments.csv)
    command = [sys.executable, "-B", str(PROJECT_ROOT / "main.py"), *arguments.main_args]
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, start_new_session=True)
    started_at = previous_at = time.monotonic()
    previous_cpu = sum(item.cpu_seconds for item in _sample(process.pid))
    peak_pss = peak_rss = 0
    peak_cpu = 0.0
    samples = 0

    print(f"YKİ started (PID {process.pid}). Press Ctrl+C to stop.")
    print(" ELAPSED | CPU     | PSS       | RSS       | PROCESSES")
    try:
        while process.poll() is None:
            time.sleep(arguments.interval)
            now = time.monotonic()
            records = _sample(process.pid)
            if not records:
                continue
            cpu_seconds = sum(item.cpu_seconds for item in records)
            pss_bytes = sum(item.pss_bytes for item in records)
            rss_bytes = sum(item.rss_bytes for item in records)
            cpu_percent = max(0.0, (cpu_seconds - previous_cpu) / max(0.001, now - previous_at) * 100.0)
            peak_cpu = max(peak_cpu, cpu_percent)
            peak_pss = max(peak_pss, pss_bytes)
            peak_rss = max(peak_rss, rss_bytes)
            samples += 1
            elapsed = now - started_at
            print(
                f" {elapsed:7.1f}s | {cpu_percent:6.1f}% | {pss_bytes / MIB:7.1f} MiB | "
                f"{rss_bytes / MIB:7.1f} MiB | {len(records):3d}",
                flush=True,
            )
            if csv_writer is not None:
                csv_writer.writerow((f"{elapsed:.3f}", f"{cpu_percent:.3f}", f"{pss_bytes / MIB:.3f}", f"{rss_bytes / MIB:.3f}", len(records)))
                csv_handle.flush()  # type: ignore[union-attr]
            if arguments.details_every and samples % arguments.details_every == 0:
                for item in sorted(records, key=lambda value: value.pss_bytes, reverse=True):
                    print(
                        f"           PID {item.pid:<7} {item.role:<20} "
                        f"PSS {item.pss_bytes / MIB:7.1f} MiB  RSS {item.rss_bytes / MIB:7.1f} MiB"
                    )
            previous_at = now
            previous_cpu = cpu_seconds
    except KeyboardInterrupt:
        print("\nStopping YKI...", flush=True)
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    finally:
        if csv_handle is not None:
            csv_handle.close()

    return_code = process.wait()
    print(
        f"Summary: {samples} samples, peak CPU {peak_cpu:.1f}%, "
        f"peak PSS {peak_pss / MIB:.1f} MiB, peak RSS {peak_rss / MIB:.1f} MiB, "
        f"exit code {return_code}.",
        flush=True,
    )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
