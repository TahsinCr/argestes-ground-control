from __future__ import annotations

import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from src.core.events import Subscription
from src.core.mavlink import MavlinkClient


_FTP_HEADER = struct.Struct("<HBBBBBBI")
_PARAM_HEADER = struct.Struct("<HHH")
_FTP_PAYLOAD_SIZE = 251
_FTP_DATA_SIZE = _FTP_PAYLOAD_SIZE - _FTP_HEADER.size
_PARAM_MAGIC_WITH_DEFAULTS = 0x671C
_OP_TERMINATE_SESSION = 1
_OP_RESET_SESSIONS = 2
_OP_OPEN_FILE_RO = 4
_OP_READ_FILE = 5
_OP_ACK = 128
_OP_NACK = 129
_MAX_PARAMETER_FILE_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _FtpOperation:
    sequence: int
    session: int
    opcode: int
    request_opcode: int
    offset: int
    payload: bytes


def decode_parameter_defaults(data: bytes) -> dict[str, object]:
    """Decode ArduPilot's packed parameter file containing compiled defaults."""

    if len(data) < _PARAM_HEADER.size:
        raise ValueError("Packed parameter data is incomplete")
    magic, _stored_count, total_count = _PARAM_HEADER.unpack_from(data)
    if magic != _PARAM_MAGIC_WITH_DEFAULTS:
        raise ValueError("Packed parameter data does not contain defaults")

    type_formats = {
        1: (1, "b"),
        2: (2, "h"),
        3: (4, "i"),
        4: (4, "f"),
    }
    defaults: dict[str, object] = {}
    cursor = _PARAM_HEADER.size
    previous_name = b""

    while cursor < len(data):
        while cursor < len(data) and data[cursor] == 0:
            cursor += 1
        if cursor >= len(data):
            break
        if cursor + 2 > len(data):
            raise ValueError("Packed parameter record header is incomplete")

        encoded_type, encoded_name = struct.unpack_from("<BB", data, cursor)
        cursor += 2
        flags = encoded_type >> 4
        parameter_type = encoded_type & 0x0F
        if parameter_type not in type_formats:
            raise ValueError(f"Unsupported packed parameter type: {parameter_type}")
        value_size, value_format = type_formats[parameter_type]
        suffix_size = (encoded_name >> 4) + 1
        common_size = encoded_name & 0x0F
        if common_size > len(previous_name) or cursor + suffix_size > len(data):
            raise ValueError("Invalid packed parameter name")

        name_bytes = previous_name[:common_size] + data[cursor : cursor + suffix_size]
        cursor += suffix_size
        has_distinct_default = bool(flags & 0x01)
        field_count = 2 if has_distinct_default else 1
        required = value_size * field_count
        if cursor + required > len(data):
            raise ValueError("Packed parameter value is incomplete")
        values = struct.unpack_from("<" + value_format * field_count, data, cursor)
        cursor += required

        name = name_bytes.decode("ascii", errors="strict")
        defaults[name] = values[1] if has_distinct_default else values[0]
        previous_name = name_bytes

    if len(defaults) != total_count:
        raise ValueError(
            f"Packed parameter count mismatch: decoded {len(defaults)}, expected {total_count}"
        )
    return defaults


class MavlinkParameterDefaultSource:
    """Read ArduPilot defaults over MAVFTP without creating another MAVLink reader."""

    REMOTE_PATH = "@PARAM/param.pck?withdefaults=1"

    def __init__(
        self,
        client: MavlinkClient,
        *,
        timeout: float = 3.0,
        retries: int = 2,
    ) -> None:
        self._client = client
        self._timeout = max(0.25, float(timeout))
        self._retries = max(1, int(retries))
        self._condition = threading.Condition()
        self._responses: deque[_FtpOperation] = deque()
        self._sequence = 0
        self._subscription: Subscription | None = None

    def load(self) -> dict[str, object]:
        if not self._client.is_connected:
            raise ConnectionError("MAVLink connection is unavailable")
        with self._condition:
            self._responses.clear()
        self._subscription = self._client.subscribe(
            self._receive,
            "FILE_TRANSFER_PROTOCOL",
        )
        session: int | None = None
        try:
            reset = self._request(
                session=0,
                opcode=_OP_RESET_SESSIONS,
                offset=0,
            )
            if reset.opcode != _OP_ACK:
                raise RuntimeError("ArduPilot did not reset the MAVFTP session")
            path = self.REMOTE_PATH.encode("ascii")
            opened = self._request(
                session=0,
                opcode=_OP_OPEN_FILE_RO,
                offset=0,
                payload=path,
            )
            session = opened.session
            if opened.opcode != _OP_ACK or len(opened.payload) < 4:
                raise RuntimeError("ArduPilot did not open the packed parameter file")
            file_size = struct.unpack_from("<I", opened.payload)[0]
            if not 0 < file_size <= _MAX_PARAMETER_FILE_SIZE:
                raise ValueError(f"Invalid packed parameter file size: {file_size}")

            chunks = bytearray()
            while len(chunks) < file_size:
                offset = len(chunks)
                requested = min(_FTP_DATA_SIZE, file_size - offset)
                response = self._request(
                    session=session,
                    opcode=_OP_READ_FILE,
                    offset=offset,
                    requested_size=requested,
                )
                if response.opcode != _OP_ACK:
                    raise RuntimeError(f"MAVFTP read was rejected at offset {offset}")
                if response.offset != offset or not response.payload:
                    raise RuntimeError(
                        f"Invalid MAVFTP read response at offset {offset}"
                    )
                chunks.extend(response.payload[:requested])
            return decode_parameter_defaults(bytes(chunks[:file_size]))
        finally:
            if session is not None:
                try:
                    self._send(
                        session=session,
                        opcode=_OP_TERMINATE_SESSION,
                        offset=0,
                    )
                except Exception:
                    pass
            subscription = self._subscription
            self._subscription = None
            if subscription is not None:
                subscription.cancel()

    def _request(
        self,
        *,
        session: int,
        opcode: int,
        offset: int,
        payload: bytes = b"",
        requested_size: int | None = None,
    ) -> _FtpOperation:
        for _attempt in range(self._retries):
            sequence = self._send(
                session=session,
                opcode=opcode,
                offset=offset,
                payload=payload,
                requested_size=requested_size,
            )
            try:
                return self._wait_for_response(
                    sequence=sequence,
                    session=session,
                    request_opcode=opcode,
                    offset=offset,
                )
            except TimeoutError:
                continue
        raise TimeoutError(
            f"MAVFTP request timed out (opcode={opcode}, offset={offset})"
        )

    def _send(
        self,
        *,
        session: int,
        opcode: int,
        offset: int,
        payload: bytes = b"",
        requested_size: int | None = None,
    ) -> int:
        if len(payload) > _FTP_DATA_SIZE:
            raise ValueError("MAVFTP payload is too large")
        sequence = self._sequence
        self._sequence = (self._sequence + 1) & 0xFFFF
        size = len(payload) if requested_size is None else int(requested_size)
        packet = bytearray(
            _FTP_HEADER.pack(sequence, session, opcode, size, 0, 0, 0, offset)
        )
        packet.extend(payload)
        packet.extend(b"\x00" * (_FTP_PAYLOAD_SIZE - len(packet)))
        self._client.call_mav(
            "file_transfer_protocol_send",
            0,
            self._client.connection.target_system,
            self._client.connection.target_component,
            packet,
        )
        return sequence

    def _wait_for_response(
        self,
        *,
        sequence: int,
        session: int,
        request_opcode: int,
        offset: int,
    ) -> _FtpOperation:
        deadline = time.monotonic() + self._timeout
        expected_sequence = (sequence + 1) & 0xFFFF
        with self._condition:
            while True:
                for response in tuple(self._responses):
                    if (
                        response.sequence == expected_sequence
                        and response.request_opcode == request_opcode
                        and response.session == session
                        and (
                            request_opcode != _OP_READ_FILE or response.offset == offset
                        )
                    ):
                        self._responses.remove(response)
                        return response
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("MAVFTP response timed out")
                self._condition.wait(remaining)

    def _receive(self, message: Any) -> None:
        try:
            source_system = int(getattr(message, "get_srcSystem", lambda: 0)() or 0)
            source_component = int(
                getattr(message, "get_srcComponent", lambda: 0)() or 0
            )
            if source_system not in {0, self._client.connection.target_system}:
                return
            if source_component not in {0, self._client.connection.target_component}:
                return
            raw_payload = bytes(message.payload)
            if len(raw_payload) < _FTP_HEADER.size:
                return
            (
                sequence,
                session,
                opcode,
                size,
                request_opcode,
                _burst_complete,
                _padding,
                offset,
            ) = _FTP_HEADER.unpack_from(raw_payload)
            if opcode not in {_OP_ACK, _OP_NACK} or size > _FTP_DATA_SIZE:
                return
            response = _FtpOperation(
                sequence=sequence,
                session=session,
                opcode=opcode,
                request_opcode=request_opcode,
                offset=offset,
                payload=raw_payload[_FTP_HEADER.size : _FTP_HEADER.size + size],
            )
        except (AttributeError, TypeError, ValueError, struct.error):
            return
        with self._condition:
            self._responses.append(response)
            while len(self._responses) > 32:
                self._responses.popleft()
            self._condition.notify_all()


__all__ = ["MavlinkParameterDefaultSource", "decode_parameter_defaults"]
