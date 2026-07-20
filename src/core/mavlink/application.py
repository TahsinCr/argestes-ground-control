from __future__ import annotations

import json
import secrets
import struct
import threading
import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.abstracts import Service
from src.core.events import EventStream, Subscription
from src.core.mavlink.client import MavlinkClient
from src.core.mavlink.filter import (
    MavlinkMessageFilter,
    mavlink_source_component,
    mavlink_source_system,
)


V2_EXTENSION_PAYLOAD_SIZE = 249
DEFAULT_APPLICATION_NETWORK = 77
DEFAULT_APPLICATION_MESSAGE_TYPE = 42_000

_MAGIC = b"MA"
_VERSION = 1
_HEADER = struct.Struct("!2sBBIHHHI")
_CHUNK_SIZE = V2_EXTENSION_PAYLOAD_SIZE - _HEADER.size
_MAX_FRAGMENT_COUNT = 72
_MAX_PACKET_BYTES = _CHUNK_SIZE * _MAX_FRAGMENT_COUNT


class MavlinkApplicationProtocolError(ValueError):
    """Uygulama kanalındaki bozuk veya uyumsuz bir paketi bildirir."""


@dataclass(frozen=True, slots=True)
class MavlinkApplicationPacket:
    """MAVLink taşıyıcısından bağımsız, genişletilebilir uygulama paketi."""

    packet_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    packet_id: int = field(default_factory=lambda: secrets.randbits(32) or 1)
    sent_at: float = field(default_factory=time.time)
    source_system: int | None = None
    source_component: int | None = None

    def __post_init__(self) -> None:
        normalized_type = self.packet_type.strip().lower()
        if not normalized_type:
            raise ValueError("Uygulama paket tipi boş olamaz")
        if not 0 < int(self.packet_id) <= 0xFFFFFFFF:
            raise ValueError("Uygulama paket kimliği 1..2^32-1 aralığında olmalı")
        object.__setattr__(self, "packet_type", normalized_type)
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "packet_id", int(self.packet_id))
        object.__setattr__(self, "sent_at", float(self.sent_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_type": self.packet_type,
            "payload": dict(self.payload),
            "packet_id": self.packet_id,
            "sent_at": self.sent_at,
            "source_system": self.source_system,
            "source_component": self.source_component,
        }


@dataclass(frozen=True, slots=True)
class _Fragment:
    packet_id: int
    index: int
    count: int
    checksum: int
    chunk: bytes


@dataclass(slots=True)
class _Assembly:
    count: int
    checksum: int
    created_monotonic: float
    fragments: dict[int, bytes] = field(default_factory=dict)


class MavlinkApplicationCodec:
    """V2_EXTENSION'ın 249 baytlık alanında JSON paketlerini güvenle parçalar."""

    max_packet_bytes = _MAX_PACKET_BYTES
    chunk_size = _CHUNK_SIZE

    @classmethod
    def encode(cls, packet: MavlinkApplicationPacket) -> tuple[bytes, ...]:
        try:
            body = json.dumps(
                {
                    "type": packet.packet_type,
                    "payload": packet.payload,
                    "sent_at": packet.sent_at,
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise MavlinkApplicationProtocolError(
                f"Uygulama paketi JSON olarak kodlanamadı: {exc}"
            ) from exc
        if not body:
            raise MavlinkApplicationProtocolError("Uygulama paketi boş olamaz")
        if len(body) > cls.max_packet_bytes:
            raise MavlinkApplicationProtocolError(
                f"Uygulama paketi çok büyük: {len(body)} > {cls.max_packet_bytes} bayt"
            )
        count = max(1, (len(body) + cls.chunk_size - 1) // cls.chunk_size)
        checksum = zlib.crc32(body) & 0xFFFFFFFF
        fragments: list[bytes] = []
        for index in range(count):
            chunk = body[index * cls.chunk_size : (index + 1) * cls.chunk_size]
            fragments.append(
                _HEADER.pack(
                    _MAGIC,
                    _VERSION,
                    0,
                    packet.packet_id,
                    index,
                    count,
                    len(chunk),
                    checksum,
                )
                + chunk
            )
        return tuple(fragments)

    @classmethod
    def decode_fragment(cls, payload: bytes | bytearray | list[int] | tuple[int, ...]) -> _Fragment:
        raw = bytes(payload)
        if len(raw) < _HEADER.size:
            raise MavlinkApplicationProtocolError("Uygulama fragment başlığı eksik")
        magic, version, _flags, packet_id, index, count, chunk_length, checksum = (
            _HEADER.unpack_from(raw)
        )
        if magic != _MAGIC:
            raise MavlinkApplicationProtocolError("Uygulama fragment imzası geçersiz")
        if version != _VERSION:
            raise MavlinkApplicationProtocolError(
                f"Desteklenmeyen uygulama protokol sürümü: {version}"
            )
        if not 0 < packet_id <= 0xFFFFFFFF:
            raise MavlinkApplicationProtocolError("Uygulama paket kimliği geçersiz")
        if not 1 <= count <= _MAX_FRAGMENT_COUNT or index >= count:
            raise MavlinkApplicationProtocolError("Uygulama fragment sırası geçersiz")
        if chunk_length > cls.chunk_size or _HEADER.size + chunk_length > len(raw):
            raise MavlinkApplicationProtocolError("Uygulama fragment uzunluğu geçersiz")
        return _Fragment(
            packet_id,
            index,
            count,
            checksum,
            raw[_HEADER.size : _HEADER.size + chunk_length],
        )

    @staticmethod
    def decode_packet(
        packet_id: int,
        body: bytes,
        *,
        source_system: int | None = None,
        source_component: int | None = None,
    ) -> MavlinkApplicationPacket:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MavlinkApplicationProtocolError(
                f"Uygulama paket gövdesi çözülemedi: {exc}"
            ) from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("payload", {}), dict):
            raise MavlinkApplicationProtocolError("Uygulama paket gövdesi nesne olmalı")
        return MavlinkApplicationPacket(
            str(decoded.get("type", "")),
            decoded.get("payload", {}),
            packet_id,
            float(decoded.get("sent_at", 0.0)),
            source_system,
            source_component,
        )


class MavlinkApplicationAssembler:
    """Sırası değişebilen fragmentleri kaynak kimliği bazında tek pakette toplar."""

    def __init__(self, *, fragment_timeout: float = 10.0) -> None:
        if fragment_timeout <= 0:
            raise ValueError("Fragment zaman aşımı pozitif olmalı")
        self._fragment_timeout = float(fragment_timeout)
        self._assemblies: dict[tuple[int, int, int], _Assembly] = {}
        self._completed: dict[tuple[int, int, int], float] = {}
        self._lock = threading.RLock()

    def accept(
        self,
        payload: bytes | bytearray | list[int] | tuple[int, ...],
        *,
        source_system: int | None = None,
        source_component: int | None = None,
    ) -> MavlinkApplicationPacket | None:
        fragment = MavlinkApplicationCodec.decode_fragment(payload)
        key = (source_system or 0, source_component or 0, fragment.packet_id)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if key in self._completed:
                return None
            assembly = self._assemblies.get(key)
            if assembly is None:
                assembly = _Assembly(fragment.count, fragment.checksum, now)
                self._assemblies[key] = assembly
            elif assembly.count != fragment.count or assembly.checksum != fragment.checksum:
                self._assemblies.pop(key, None)
                raise MavlinkApplicationProtocolError(
                    "Aynı kimlikte uyuşmayan uygulama fragmentleri alındı"
                )
            previous = assembly.fragments.get(fragment.index)
            if previous is not None and previous != fragment.chunk:
                self._assemblies.pop(key, None)
                raise MavlinkApplicationProtocolError("Tekrarlanan fragment içeriği uyuşmuyor")
            assembly.fragments[fragment.index] = fragment.chunk
            if len(assembly.fragments) != assembly.count:
                return None
            body = b"".join(assembly.fragments[index] for index in range(assembly.count))
            self._assemblies.pop(key, None)
            if zlib.crc32(body) & 0xFFFFFFFF != assembly.checksum:
                raise MavlinkApplicationProtocolError("Uygulama paketi CRC doğrulamasını geçemedi")
            self._completed[key] = now
        return MavlinkApplicationCodec.decode_packet(
            fragment.packet_id,
            body,
            source_system=source_system,
            source_component=source_component,
        )

    def clear(self) -> None:
        with self._lock:
            self._assemblies.clear()
            self._completed.clear()

    def _prune(self, now: float) -> None:
        deadline = now - self._fragment_timeout
        self._assemblies = {
            key: value
            for key, value in self._assemblies.items()
            if value.created_monotonic >= deadline
        }
        self._completed = {
            key: completed_at
            for key, completed_at in self._completed.items()
            if completed_at >= deadline
        }


class MavlinkApplicationChannel(Service):
    """Mevcut MAVLink router üzerinde hedefli, çift yönlü uygulama kanalı.

    Yeni bir seri okuyucu açmaz. Uçuş mesajlarıyla aynı MAVLink bağlantısını ve
    V2_EXTENSION'ın hedef system/component alanlarını kullanır.
    """

    def __init__(
        self,
        client: MavlinkClient,
        *,
        network_id: int = DEFAULT_APPLICATION_NETWORK,
        message_type: int = DEFAULT_APPLICATION_MESSAGE_TYPE,
        target_system: int = 0,
        target_component: int = 0,
        local_system: int | None = None,
        local_component: int | None = None,
        source_systems: int | tuple[int, ...] | None = None,
        source_components: int | tuple[int, ...] | None = None,
        fragment_timeout: float = 10.0,
    ) -> None:
        for name, value, maximum in (
            ("network_id", network_id, 255),
            ("target_system", target_system, 255),
            ("target_component", target_component, 255),
        ):
            if not 0 <= int(value) <= maximum:
                raise ValueError(f"{name} 0..{maximum} aralığında olmalı")
        if not 0 <= int(message_type) <= 65535:
            raise ValueError("V2_EXTENSION message_type 0..65535 aralığında olmalı")
        self._client = client
        self.network_id = int(network_id)
        self.message_type = int(message_type)
        self.target_system = int(target_system)
        self.target_component = int(target_component)
        self.local_system = (
            client.endpoint.source_system if local_system is None else int(local_system)
        )
        self.local_component = (
            client.endpoint.source_component if local_component is None else int(local_component)
        )
        self.packets = EventStream[MavlinkApplicationPacket]()
        self.errors = EventStream[Exception]()
        self._assembler = MavlinkApplicationAssembler(fragment_timeout=fragment_timeout)
        self._source_systems = source_systems
        self._source_components = source_components
        self._subscription: Subscription | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._subscription is not None

    def start(self) -> None:
        with self._lock:
            if self._subscription is not None:
                return
            self._subscription = self._client.subscribe(
                self._on_message,
                MavlinkMessageFilter.for_types(
                    "V2_EXTENSION",
                    source_systems=self._source_systems,
                    source_components=self._source_components,
                    predicate=self._matches_channel,
                ),
            )

    def stop(self) -> None:
        with self._lock:
            subscription = self._subscription
            self._subscription = None
        if subscription is not None:
            subscription.cancel()
        self._assembler.clear()

    def send(
        self,
        packet_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        packet_id: int | None = None,
        target_system: int | None = None,
        target_component: int | None = None,
    ) -> MavlinkApplicationPacket:
        packet = MavlinkApplicationPacket(
            packet_type,
            dict(payload or {}),
            packet_id if packet_id is not None else (secrets.randbits(32) or 1),
        )
        system = self.target_system if target_system is None else int(target_system)
        component = self.target_component if target_component is None else int(target_component)
        if not 0 <= system <= 255 or not 0 <= component <= 255:
            raise ValueError("Hedef system/component 0..255 aralığında olmalı")
        for fragment in MavlinkApplicationCodec.encode(packet):
            padded = list(fragment) + [0] * (V2_EXTENSION_PAYLOAD_SIZE - len(fragment))
            self._client.call_mav(
                "v2_extension_send",
                self.network_id,
                system,
                component,
                self.message_type,
                padded,
            )
        return packet

    def _matches_channel(self, message: Any) -> bool:
        try:
            if int(message.target_network) != self.network_id:
                return False
            if int(message.message_type) != self.message_type:
                return False
            if int(message.target_system) not in {0, self.local_system}:
                return False
            return int(message.target_component) in {0, self.local_component}
        except (AttributeError, TypeError, ValueError):
            return False

    def _on_message(self, message: Any) -> None:
        try:
            packet = self._assembler.accept(
                message.payload,
                source_system=mavlink_source_system(message),
                source_component=mavlink_source_component(message),
            )
        except Exception as exc:
            self.errors.publish_isolated(exc)
            return
        if packet is not None:
            self.packets.publish_isolated(packet)
