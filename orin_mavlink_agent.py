#!/usr/bin/env python3
"""Orin <-> YKI application channel over the Pixhawk/RFD MAVLink route.

This file is intentionally standalone: copy it to the Orin and run it with a
Python environment that only needs ``pymavlink``. It must own its MAVLink
endpoint, or connect to a dedicated mavlink-router UDP endpoint if another
process already owns the Pixhawk serial port.

Example:
    python3 orin_mavlink_agent.py --endpoint /dev/ttyTHS1 --baud 921600
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import shlex
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pymavlink import mavutil


PAYLOAD_SIZE = 249
MAGIC = b"MA"
PROTOCOL_VERSION = 1
HEADER = struct.Struct("!2sBBIHHHI")
CHUNK_SIZE = PAYLOAD_SIZE - HEADER.size
MAX_FRAGMENTS = 72


@dataclass(frozen=True, slots=True)
class Packet:
    packet_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    packet_id: int = field(default_factory=lambda: secrets.randbits(32) or 1)
    sent_at: float = field(default_factory=time.time)
    source_system: int = 0
    source_component: int = 0


@dataclass(slots=True)
class Assembly:
    fragment_count: int
    checksum: int
    created_at: float
    fragments: dict[int, bytes] = field(default_factory=dict)


class PacketCodec:
    @staticmethod
    def encode(packet: Packet) -> tuple[bytes, ...]:
        body = json.dumps(
            {
                "type": packet.packet_type.strip().lower(),
                "payload": dict(packet.payload),
                "sent_at": float(packet.sent_at),
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        max_size = CHUNK_SIZE * MAX_FRAGMENTS
        if not body or len(body) > max_size:
            raise ValueError(f"packet size must be 1..{max_size} bytes")
        fragment_count = max(1, (len(body) + CHUNK_SIZE - 1) // CHUNK_SIZE)
        checksum = zlib.crc32(body) & 0xFFFFFFFF
        fragments: list[bytes] = []
        for index in range(fragment_count):
            chunk = body[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
            fragments.append(
                HEADER.pack(
                    MAGIC,
                    PROTOCOL_VERSION,
                    0,
                    packet.packet_id,
                    index,
                    fragment_count,
                    len(chunk),
                    checksum,
                )
                + chunk
            )
        return tuple(fragments)

    @staticmethod
    def decode_body(packet_id: int, body: bytes, source_system: int, source_component: int) -> Packet:
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict) or not isinstance(decoded.get("payload", {}), dict):
            raise ValueError("packet body is not an object")
        packet_type = str(decoded.get("type", "")).strip().lower()
        if not packet_type:
            raise ValueError("packet type is empty")
        return Packet(
            packet_type,
            dict(decoded.get("payload", {})),
            packet_id,
            float(decoded.get("sent_at", 0.0)),
            source_system,
            source_component,
        )


class PacketAssembler:
    def __init__(self, timeout_s: float = 10.0) -> None:
        self.timeout_s = float(timeout_s)
        self.assemblies: dict[tuple[int, int, int], Assembly] = {}
        self.completed: dict[tuple[int, int, int], float] = {}

    def accept(self, message: Any) -> Packet | None:
        raw = bytes(message.payload)
        if len(raw) < HEADER.size:
            raise ValueError("fragment header is missing")
        magic, version, _flags, packet_id, index, count, length, checksum = HEADER.unpack_from(raw)
        if magic != MAGIC or version != PROTOCOL_VERSION:
            raise ValueError("fragment signature or version is invalid")
        if not 1 <= count <= MAX_FRAGMENTS or index >= count:
            raise ValueError("fragment index/count is invalid")
        if length > CHUNK_SIZE or HEADER.size + length > len(raw):
            raise ValueError("fragment length is invalid")
        source_system = int(message.get_srcSystem())
        source_component = int(message.get_srcComponent())
        key = (source_system, source_component, int(packet_id))
        now = time.monotonic()
        self._prune(now)
        if key in self.completed:
            return None
        assembly = self.assemblies.get(key)
        if assembly is None:
            assembly = Assembly(int(count), int(checksum), now)
            self.assemblies[key] = assembly
        elif assembly.fragment_count != count or assembly.checksum != checksum:
            self.assemblies.pop(key, None)
            raise ValueError("fragments with the same packet id do not match")
        chunk = raw[HEADER.size : HEADER.size + length]
        previous = assembly.fragments.get(int(index))
        if previous is not None and previous != chunk:
            self.assemblies.pop(key, None)
            raise ValueError("duplicate fragment content does not match")
        assembly.fragments[int(index)] = chunk
        if len(assembly.fragments) != assembly.fragment_count:
            return None
        body = b"".join(assembly.fragments[item] for item in range(assembly.fragment_count))
        self.assemblies.pop(key, None)
        if zlib.crc32(body) & 0xFFFFFFFF != assembly.checksum:
            raise ValueError("packet CRC check failed")
        self.completed[key] = now
        return PacketCodec.decode_body(packet_id, body, source_system, source_component)

    def _prune(self, now: float) -> None:
        deadline = now - self.timeout_s
        self.assemblies = {
            key: value for key, value in self.assemblies.items() if value.created_at >= deadline
        }
        self.completed = {
            key: value for key, value in self.completed.items() if value >= deadline
        }


Handler = Callable[[Packet], tuple[bool, str, dict[str, Any]]]
TaskHandler = Callable[[dict[str, Any]], tuple[bool, str, dict[str, Any]]]


class ConfiguredCommandExecutor:
    """Runs explicitly configured task commands without invoking a shell."""

    def __init__(
        self,
        config_path: Path,
        commands: dict[str, str | list[str]],
        *,
        timeout_s: float = 30.0,
        output_limit: int = 2_000,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("configured command timeout must be positive")
        self.config_path = config_path
        self.base_dir = config_path.parent
        self.timeout_s = float(timeout_s)
        self.output_limit = int(output_limit)
        self.commands = {
            str(name).strip().upper(): self._resolve_command(command)
            for name, command in commands.items()
            if str(name).strip()
        }

    def handler_for(self, task: str) -> TaskHandler:
        normalized = task.strip().upper()

        def execute(parameters: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
            return self.execute(normalized, parameters)

        return execute

    def execute(
        self,
        task: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        normalized = task.strip().upper()
        command = self.commands.get(normalized)
        if command is None:
            logging.error("configured command not found task=%s", normalized or task)
            return False, f"configured command not found: {task}", {
                "task": normalized,
                "execution": "not_configured",
            }
        payload_json = json.dumps(
            parameters,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        environment = os.environ.copy()
        environment.update(
            ORIN_COMMAND=normalized,
            ORIN_PAYLOAD_JSON=payload_json,
            ORIN_CONFIG_PATH=str(self.config_path),
        )
        started = time.monotonic()
        logging.info(
            "configured command starting task=%s executable=%s",
            normalized,
            command[0],
        )
        try:
            completed = subprocess.run(
                command,
                input=payload_json,
                text=True,
                capture_output=True,
                cwd=self.base_dir,
                env=environment,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = self._tail(exc.stdout)
            stderr = self._tail(exc.stderr)
            duration_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            logging.error(
                "configured command timeout task=%s timeout=%.1fs duration_ms=%.1f",
                normalized,
                self.timeout_s,
                duration_ms,
            )
            self._log_output(normalized, stdout, stderr)
            return False, f"command timed out after {self.timeout_s:g} seconds", {
                "task": normalized,
                "execution": "timeout",
                "duration_ms": duration_ms,
                "stdout": stdout,
                "stderr": stderr,
            }
        except OSError as exc:
            duration_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            logging.error(
                "configured command could not start task=%s error=%s",
                normalized,
                exc,
            )
            return False, f"command could not start: {exc}", {
                "task": normalized,
                "execution": "start_failed",
                "duration_ms": duration_ms,
                "stderr": str(exc),
            }
        stdout = self._tail(completed.stdout)
        stderr = self._tail(completed.stderr)
        accepted = completed.returncode == 0
        duration_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        logging.log(
            logging.INFO if accepted else logging.ERROR,
            "configured command finished task=%s status=%s exit_code=%d duration_ms=%.1f",
            normalized,
            "success" if accepted else "failed",
            completed.returncode,
            duration_ms,
        )
        self._log_output(normalized, stdout, stderr)
        return accepted, (
            f"command completed: {normalized}"
            if accepted
            else f"command failed with exit code {completed.returncode}: {normalized}"
        ), {
            "task": normalized,
            "execution": "completed" if accepted else "failed",
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "stdout": stdout,
            "stderr": stderr,
        }

    def validate(self) -> None:
        for task, command in self.commands.items():
            paths = [Path(command[0])]
            if len(command) > 1 and Path(command[1]).suffix.lower() == ".py":
                paths.append(Path(command[1]))
            for executable in paths:
                if not executable.exists():
                    raise FileNotFoundError(f"{task} executable does not exist: {executable}")
                if executable.is_dir():
                    raise ValueError(f"{task} executable is a directory: {executable}")

    def _resolve_command(self, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            command = shlex.split(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            command = list(value)
        else:
            raise ValueError("configured command must be a string or string list")
        if not command:
            raise ValueError("configured command cannot be empty")
        executable = Path(command[0]).expanduser()
        if not executable.is_absolute():
            executable = (self.base_dir / executable).resolve()
        command[0] = str(executable)
        if executable.suffix.lower() == ".py":
            command.insert(0, sys.executable)
        return command

    def _tail(self, value: str | bytes | None) -> str:
        if value is None:
            return ""
        text = value.decode(errors="replace") if isinstance(value, bytes) else str(value)
        return text[-self.output_limit :]

    @staticmethod
    def _log_output(task: str, stdout: str, stderr: str) -> None:
        if stdout:
            logging.info("configured command stdout task=%s output=%s", task, stdout)
        if stderr:
            logging.error("configured command stderr task=%s output=%s", task, stderr)


class OrinMavlinkAgent:
    """Small handler registry and heartbeat agent for vehicle-side applications."""

    def __init__(
        self,
        endpoint: str,
        *,
        baud: int = 921_600,
        system_id: int = 1,
        component_id: int = 191,
        gcs_system_id: int = 255,
        gcs_component_id: int = 0,
        network_id: int = 77,
        message_type: int = 42_000,
        app_heartbeat_s: float = 5.0,
        dialect: str = "ardupilotmega",
        worker_count: int = 2,
        pixhawk_heartbeat_timeout_s: float = 15.0,
        reconnect_seconds: float = 5.0,
    ) -> None:
        self.endpoint = endpoint
        self.baud = int(baud)
        self.system_id = int(system_id)
        self.component_id = int(component_id)
        self.gcs_system_id = int(gcs_system_id)
        self.gcs_component_id = int(gcs_component_id)
        self.network_id = int(network_id)
        self.message_type = int(message_type)
        self.app_heartbeat_s = float(app_heartbeat_s)
        self.dialect = dialect
        self.pixhawk_heartbeat_timeout_s = float(pixhawk_heartbeat_timeout_s)
        if self.pixhawk_heartbeat_timeout_s <= 0:
            raise ValueError("Pixhawk heartbeat timeout must be positive")
        self.reconnect_seconds = float(reconnect_seconds)
        if self.reconnect_seconds <= 0:
            raise ValueError("reconnect delay must be positive")
        self.connection: Any | None = None
        self.assembler = PacketAssembler(max(10.0, app_heartbeat_s * 2.0))
        self.handlers: dict[str, Handler] = {}
        self.task_handlers: dict[str, TaskHandler] = {}
        self.active_task = ""
        self.health = "ok"
        self.camera_state: dict[str, Any] = {}
        self.started_at = time.monotonic()
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._workers = ThreadPoolExecutor(max_workers=max(1, worker_count), thread_name_prefix="OrinAction")
        self.register_handler("system.ping", self._handle_ping)
        self.register_handler("system.status.request", self._handle_status_request)
        self.register_handler("task.start", self._handle_task_start)
        self.register_handler("task.stop", self._handle_task_stop)

    def register_handler(self, packet_type: str, handler: Handler) -> None:
        normalized = packet_type.strip().lower()
        if not normalized or not callable(handler):
            raise ValueError("packet type and callable handler are required")
        self.handlers[normalized] = handler

    def register_task_handler(self, task: str, handler: TaskHandler) -> None:
        """Attach the real camera/vision/mission implementation for a named task."""
        normalized = task.strip().upper()
        if not normalized or not callable(handler):
            raise ValueError("task name and callable handler are required")
        self.task_handlers[normalized] = handler

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._run_connection_cycle()
                except Exception as exc:
                    self._close_connection()
                    if self._stop.is_set():
                        break
                    logging.warning(
                        "MAVLink connection unavailable: %s; retrying in %.1f seconds",
                        exc,
                        self.reconnect_seconds,
                    )
                    self._stop.wait(self.reconnect_seconds)
        finally:
            self._stop.set()
            self._close_connection()
            self._workers.shutdown(wait=False, cancel_futures=True)
            logging.info("Orin MAVLink agent stopped")

    def _run_connection_cycle(self) -> None:
        logging.info(
            "opening MAVLink endpoint=%s baud=%d source=%d:%d",
            self.endpoint,
            self.baud,
            self.system_id,
            self.component_id,
        )
        self.connection = mavutil.mavlink_connection(
            self.endpoint,
            baud=self.baud,
            dialect=self.dialect,
            autoreconnect=True,
            source_system=self.system_id,
            source_component=self.component_id,
        )
        heartbeat = self._wait_pixhawk_heartbeat()
        if heartbeat is None:
            if self._stop.is_set():
                return
            raise TimeoutError(
                f"Pixhawk heartbeat was not received in {self.pixhawk_heartbeat_timeout_s:g} seconds"
            )
        logging.info(
            "Pixhawk heartbeat received source=%d:%d; Orin route is starting",
            int(heartbeat.get_srcSystem()),
            int(heartbeat.get_srcComponent()),
        )
        next_route_heartbeat = 0.0
        next_app_heartbeat = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_route_heartbeat:
                self._send_mavlink_heartbeat()
                next_route_heartbeat = now + 1.0
            if now >= next_app_heartbeat:
                # Pixhawk, GCS system-id'sine hedeflenen V2_EXTENSION paketini
                # her telemetri portuna yönlendirmeyebilir. Heartbeat bir durum
                # duyurusu olduğundan broadcast hedefi iki AREF portunda da
                # güvenilir biçimde görülmesini sağlar; uygulama filtreleri
                # network/message kimlikleriyle yalıtımı korur.
                packet = self._send_application_heartbeat()
                logging.info(
                    "application heartbeat sent id=%d health=%s active_task=%s",
                    packet.packet_id,
                    self.health,
                    self.active_task or "idle",
                )
                next_app_heartbeat = now + self.app_heartbeat_s
            message = self.connection.recv_match(blocking=True, timeout=0.25)
            if message is not None:
                self._receive(message)

    def _wait_pixhawk_heartbeat(self) -> Any | None:
        if self.connection is None:
            return None
        deadline = time.monotonic() + self.pixhawk_heartbeat_timeout_s
        classifier = getattr(self.connection, "probably_vehicle_heartbeat", None)
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            message = self.connection.recv_match(
                type="HEARTBEAT",
                blocking=True,
                timeout=min(0.5, remaining),
            )
            if message is None:
                continue
            if not callable(classifier) or classifier(message):
                return message
        return None

    def _close_connection(self) -> None:
        connection = self.connection
        self.connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                logging.warning("MAVLink endpoint close failed: %s", exc)

    def send_packet(
        self,
        packet_type: str,
        payload: dict[str, Any] | None = None,
        *,
        target_system: int | None = None,
        target_component: int | None = None,
    ) -> Packet:
        if self.connection is None:
            raise ConnectionError("MAVLink connection is not open")
        packet = Packet(packet_type.strip().lower(), dict(payload or {}))
        target_system = self.gcs_system_id if target_system is None else int(target_system)
        target_component = self.gcs_component_id if target_component is None else int(target_component)
        with self._send_lock:
            for fragment in PacketCodec.encode(packet):
                padded = list(fragment) + [0] * (PAYLOAD_SIZE - len(fragment))
                self.connection.mav.v2_extension_send(
                    self.network_id,
                    target_system,
                    target_component,
                    self.message_type,
                    padded,
                )
        return packet

    def _send_application_heartbeat(self) -> Packet:
        return self.send_packet(
            "system.heartbeat",
            self.status_payload(),
            target_system=0,
            target_component=0,
        )

    def status_payload(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "health": self.health,
                "active_task": self.active_task,
                "uptime_s": round(time.monotonic() - self.started_at, 1),
                "version": "1.0",
                "capabilities": sorted(
                    [*self.handlers, *(f"task:{name}" for name in self.task_handlers)]
                ),
            }

    def _receive(self, message: Any) -> None:
        if message.get_type() != "V2_EXTENSION":
            return
        try:
            if int(message.target_network) != self.network_id:
                return
            if int(message.message_type) != self.message_type:
                return
            if int(message.target_system) not in {0, self.system_id}:
                return
            if int(message.target_component) not in {0, self.component_id}:
                return
            if int(message.get_srcSystem()) != self.gcs_system_id:
                return
            packet = self.assembler.accept(message)
        except Exception as exc:
            logging.warning("invalid application fragment rejected: %s", exc)
            return
        if packet is None:
            return
        logging.info(
            "packet received id=%d type=%s source=%d:%d payload=%s",
            packet.packet_id,
            packet.packet_type,
            packet.source_system,
            packet.source_component,
            packet.payload,
        )
        self._workers.submit(self._dispatch, packet)

    def _dispatch(self, packet: Packet) -> None:
        handler = self.handlers.get(packet.packet_type)
        if handler is None:
            accepted, message, response = False, f"unsupported packet type: {packet.packet_type}", {}
        else:
            try:
                accepted, message, response = handler(packet)
            except Exception as exc:
                logging.exception("handler failed id=%d type=%s", packet.packet_id, packet.packet_type)
                accepted, message, response = False, str(exc), {}
        response_type = "system.ack" if accepted else "system.error"
        response_payload = {
            "request_id": packet.packet_id,
            "accepted": bool(accepted),
            "message": str(message),
            **dict(response),
        }
        reply = self.send_packet(
            response_type,
            response_payload,
            target_system=packet.source_system,
            target_component=packet.source_component,
        )
        logging.info(
            "response sent id=%d request_id=%d accepted=%s message=%s",
            reply.packet_id,
            packet.packet_id,
            accepted,
            message,
        )

    def _send_mavlink_heartbeat(self) -> None:
        if self.connection is None:
            return
        with self._send_lock:
            self.connection.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                mavutil.mavlink.MAV_STATE_ACTIVE,
            )

    def _handle_ping(self, _packet: Packet) -> tuple[bool, str, dict[str, Any]]:
        return True, "Orin is alive", self.status_payload()

    def _handle_status_request(self, _packet: Packet) -> tuple[bool, str, dict[str, Any]]:
        return True, "Orin status returned", self.status_payload()

    def _handle_task_start(self, packet: Packet) -> tuple[bool, str, dict[str, Any]]:
        task = str(packet.payload.get("task", "")).strip().upper()
        if not task:
            return False, "task name is required", {}
        parameters = packet.payload.get("parameters", {})
        if not isinstance(parameters, dict):
            return False, "task parameters must be an object", {}
        task_handler = self.task_handlers.get(task)
        logging.info(
            "task execution requested request_id=%d task=%s configured=%s parameters=%s",
            packet.packet_id,
            task,
            task_handler is not None,
            parameters,
        )
        handler_response: dict[str, Any] = {"execution": "state_only"}
        handler_message = f"task accepted: {task}"
        if task_handler is not None:
            accepted, handler_message, handler_response = task_handler(dict(parameters))
            if not accepted:
                logging.error(
                    "task execution failed request_id=%d task=%s message=%s details=%s",
                    packet.packet_id,
                    task,
                    handler_message,
                    handler_response,
                )
                return False, handler_message, {"task": task, **dict(handler_response)}
        with self._state_lock:
            previous = self.active_task
            self.active_task = task
        logging.info(
            "task execution accepted request_id=%d previous=%s current=%s details=%s",
            packet.packet_id,
            previous or "idle",
            task,
            handler_response,
        )
        return True, handler_message, {
            "active_task": task,
            "previous_task": previous,
            **dict(handler_response),
        }

    def _handle_task_stop(self, packet: Packet) -> tuple[bool, str, dict[str, Any]]:
        requested = str(packet.payload.get("task", "")).strip().upper()
        with self._state_lock:
            previous = self.active_task
            if requested and previous and requested != previous:
                return False, f"active task is {previous}, not {requested}", {"active_task": previous}
            self.active_task = ""
        logging.info("task stopped previous=%s", previous or "idle")
        return True, "task stopped", {"previous_task": previous, "active_task": ""}


def load_config(config_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"config JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("config root must be an object")
    for section in ("mavlink", "agent", "commands"):
        value = payload.get(section, {})
        if not isinstance(value, dict):
            raise ValueError(f"config section must be an object: {section}")
    commands = payload.get("commands", {})
    for name, command in commands.items():
        if not str(name).strip():
            raise ValueError("configured command name cannot be empty")
        if not isinstance(command, str) and not (
            isinstance(command, list) and command and all(isinstance(item, str) for item in command)
        ):
            raise ValueError(f"configured command must be a string or string list: {name}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Orin MAVLink application agent")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.json")),
        help="JSON configuration file",
    )
    parser.add_argument("--check-config", action="store_true", help="validate configuration and exit")
    parser.add_argument("--endpoint", help="override Pixhawk serial or mavlink-router endpoint")
    parser.add_argument("--baud", type=int)
    parser.add_argument("--system-id", type=int, help="same MAVLink system id as the Pixhawk")
    parser.add_argument("--component-id", type=int)
    parser.add_argument("--gcs-system-id", type=int)
    parser.add_argument("--gcs-component-id", type=int)
    parser.add_argument("--network-id", type=int)
    parser.add_argument("--message-type", type=int)
    parser.add_argument("--heartbeat-seconds", type=float)
    parser.add_argument("--dialect")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    try:
        config = load_config(config_path)
        mavlink_config = config.get("mavlink", {})
        agent_config = config.get("agent", {})
        commands = config.get("commands", {})
        command_executor = ConfiguredCommandExecutor(
            config_path,
            commands,
            timeout_s=float(agent_config.get("command_timeout_s", 30.0)),
        )
        command_executor.validate()
    except Exception as exc:
        print(f"CONFIG_ERROR {exc}", file=sys.stderr)
        return 2
    if args.check_config:
        print(
            f"CONFIG_OK path={config_path} commands={','.join(sorted(command_executor.commands)) or '-'}"
        )
        return 0
    log_level = str(args.log_level or agent_config.get("log_level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        print(f"CONFIG_ERROR unsupported log level: {log_level}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s ORIN %(message)s",
    )

    def configured(cli_value: Any, key: str, default: Any) -> Any:
        return cli_value if cli_value is not None else mavlink_config.get(key, default)

    endpoint_value = str(configured(args.endpoint, "endpoint", "/dev/ttyTHS1"))
    if endpoint_value.startswith("/dev/") and not Path(endpoint_value).exists():
        print(
            f"STARTUP_ERROR MAVLink device not found: {endpoint_value}. "
            "Run orin_mavlink_agent.py on the Orin; start the YKI with main.py.",
            file=sys.stderr,
        )
        return 2

    agent = OrinMavlinkAgent(
        endpoint_value,
        baud=int(configured(args.baud, "baud", 57_600)),
        system_id=int(configured(args.system_id, "system_id", 1)),
        component_id=int(configured(args.component_id, "component_id", 191)),
        gcs_system_id=int(configured(args.gcs_system_id, "gcs_system_id", 255)),
        gcs_component_id=int(configured(args.gcs_component_id, "gcs_component_id", 0)),
        network_id=int(configured(args.network_id, "network_id", 77)),
        message_type=int(configured(args.message_type, "message_type", 42_000)),
        app_heartbeat_s=float(
            args.heartbeat_seconds or agent_config.get("heartbeat_seconds", 5.0)
        ),
        dialect=str(args.dialect or mavlink_config.get("dialect", "ardupilotmega")),
        worker_count=int(args.workers or agent_config.get("workers", 2)),
        pixhawk_heartbeat_timeout_s=float(
            agent_config.get("pixhawk_heartbeat_timeout_s", 15.0)
        ),
        reconnect_seconds=float(agent_config.get("reconnect_seconds", 5.0)),
    )
    for task in command_executor.commands:
        agent.register_task_handler(task, command_executor.handler_for(task))
    signal.signal(signal.SIGINT, lambda _signum, _frame: agent.stop())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: agent.stop())
    try:
        agent.run()
    except Exception:
        logging.exception("Orin MAVLink agent terminated with an error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
