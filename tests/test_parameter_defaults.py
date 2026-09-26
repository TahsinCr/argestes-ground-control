from __future__ import annotations

import struct
import unittest
from types import SimpleNamespace

from src.core.events import Subscription
from extensions.mavlink.parameter_defaults import MavlinkParameterDefaultSource


class _FtpMessage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    @staticmethod
    def get_srcSystem() -> int:
        return 1

    @staticmethod
    def get_srcComponent() -> int:
        return 1


class _FakeMavlinkClient:
    is_connected = True
    connection = SimpleNamespace(target_system=1, target_component=1)

    def __init__(self, parameter_file: bytes) -> None:
        self._parameter_file = parameter_file
        self._callback = None
        self.request_opcodes: list[int] = []

    def subscribe(self, callback, message_type: str) -> Subscription:
        self._callback = callback
        self.assert_message_type = message_type
        return Subscription(1, lambda: None)

    def call_mav(self, method: str, _network: int, _system: int, _component: int, payload) -> None:
        assert method == "file_transfer_protocol_send"
        sequence, session, opcode, size, _request, _burst, _padding, offset = (
            struct.unpack_from("<HBBBBBBI", bytes(payload))
        )
        self.request_opcodes.append(opcode)
        if opcode == 2:
            response_payload = b""
        elif opcode == 4:
            response_payload = struct.pack("<I", len(self._parameter_file))
        elif opcode == 5:
            response_payload = self._parameter_file[offset : offset + size]
        else:
            return
        response = bytearray(
            struct.pack(
                "<HBBBBBBI",
                sequence + 1,
                session,
                128,
                len(response_payload),
                opcode,
                0,
                0,
                offset,
            )
        )
        response.extend(response_payload)
        response.extend(b"\x00" * (251 - len(response)))
        assert self._callback is not None
        self._callback(_FtpMessage(bytes(response)))


class ParameterDefaultSourceTests(unittest.TestCase):
    def test_defaults_are_downloaded_through_the_shared_client_subscription(self) -> None:
        packed = bytearray(struct.pack("<HHH", 0x671C, 1, 1))
        packed.extend(struct.pack("<BB", 0x14, 0x20))
        packed.extend(b"FOO")
        packed.extend(struct.pack("<ff", 17.0, 12.0))
        client = _FakeMavlinkClient(bytes(packed))

        defaults = MavlinkParameterDefaultSource(client).load()  # type: ignore[arg-type]

        self.assertEqual(defaults, {"FOO": 12.0})
        self.assertEqual(client.assert_message_type, "FILE_TRANSFER_PROTOCOL")
        self.assertEqual(client.request_opcodes[:3], [2, 4, 5])


if __name__ == "__main__":
    unittest.main()
