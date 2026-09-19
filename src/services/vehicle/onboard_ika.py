from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from src.core.events import EventBus, Subscription
from src.core.mavlink import (
    MavlinkApplicationChannel,
    MavlinkApplicationPacket,
    MavlinkApplicationPeer,
    MavlinkApplicationPeerState,
    MavlinkClient,
)
from src.models.kamikaze_approach import KamikazeApproachPlan
from src.models.onboard import OnboardMissionEvent, OnboardMissionState
from src.models.orin import OrinCommandReceipt, OrinLinkConfig
from src.models.geometry import Point
from src.services.abstracts.onboard import OnboardMissionServiceContract


class IkaOnboardMissionService(OnboardMissionServiceContract):
    """Small YKİ client for İKA controls carried by the shared MAVLink link."""

    def __init__(self, client: MavlinkClient, config: OrinLinkConfig) -> None:
        self._config = config
        self._state = OnboardMissionState(
            available_tasks=("calibration", "course"),
            last_error="İKA heartbeat bekleniyor",
        )
        self._changed = EventBus[OnboardMissionState]()
        self._events = EventBus[OnboardMissionEvent]()
        self._subscriptions: list[Subscription] = []
        channel = MavlinkApplicationChannel(
            client,
            network_id=config.network_id,
            message_type=config.message_type,
            target_system=config.system_id,
            target_component=config.component_id,
            local_system=client.endpoint.source_system,
            local_component=client.endpoint.source_component,
            source_systems=config.system_id,
            source_components=config.component_id,
            fragment_timeout=max(5.0, config.heartbeat_timeout_s),
        )
        self._peer = MavlinkApplicationPeer(
            channel,
            role="gcs",
            target_system=config.system_id,
            target_component=config.component_id,
            transport_available=lambda: client.is_connected,
            heartbeat_payload={"application": "yki", "role": "gcs"},
            heartbeat_interval=1.0,
            heartbeat_timeout=config.heartbeat_timeout_s,
        )

    @property
    def state(self) -> OnboardMissionState:
        return self._state

    @property
    def changed(self) -> EventBus[OnboardMissionState]:
        return self._changed

    @property
    def events(self) -> EventBus[OnboardMissionEvent]:
        return self._events

    def start(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions = [
            self._peer.changed.subscribe(self._on_peer_changed),
            self._peer.packets.subscribe(self._on_packet),
        ]
        self._peer.start()

    def stop(self) -> None:
        self._peer.stop()
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()

    def start_calibration(self) -> OrinCommandReceipt:
        return self._issue("calibration.start", {
            "duration_s": 5.0,
            "rotate": True,
            "speed_percent": 10.0,
            "target_degrees": 360.0,
            "timeout_s": 40.0,
        })

    def start_course(self) -> OrinCommandReceipt:
        return self._issue("course.start")

    def start_task(self, task: str, *, source: str = "ui") -> OrinCommandReceipt:
        normalized = task.strip().lower()
        if normalized == "calibration":
            return self.start_calibration()
        if normalized == "course":
            return self.start_course()
        return self._unsupported(task)

    def _issue(self, packet_type: str, payload: dict[str, Any] | None = None) -> OrinCommandReceipt:
        started = time.monotonic()
        try:
            response = self._peer.request(
                packet_type,
                payload,
                response_types=frozenset({"system.ack", "system.error"}),
                timeout=self._config.command_timeout_s,
            )
            body = _plain_mapping(response.response.payload)
            accepted = response.response.packet_type == "system.ack" and bool(
                body.get("accepted", True)
            )
            return OrinCommandReceipt(
                response.request.packet_id,
                accepted,
                str(body.get("message", "Komut tamamlandı")),
                body,
                response.round_trip_ms,
            )
        except Exception as exc:
            return OrinCommandReceipt(
                0, False, str(exc), {},
                max(0.0, (time.monotonic() - started) * 1000.0),
            )

    def _on_peer_changed(self, peer: MavlinkApplicationPeerState) -> None:
        self._publish_state(
            connected=peer.alive,
            last_error="" if peer.alive else peer.last_error,
        )

    def _on_packet(self, packet: MavlinkApplicationPacket) -> None:
        if packet.packet_type != "system.heartbeat":
            return
        payload = dict(packet.payload)
        course_value = payload.get("course", {})
        course = dict(course_value) if hasattr(course_value, "items") else {}
        enabled = bool(course.get("enabled", False))
        self._publish_state(
            connected=True,
            onboard_version=str(payload.get("version", "ika-autonom")),
            active_task="course" if enabled else "",
            phase=str(course.get("state", "idle")),
            result={"course": course},
            last_error="",
        )

    def _publish_state(self, **changes: Any) -> None:
        updated = replace(self._state, **changes)
        if updated == self._state:
            return
        self._state = updated
        self._changed.publish(updated)

    @staticmethod
    def _unsupported(task: str) -> OrinCommandReceipt:
        return OrinCommandReceipt(0, False, f"İKA komutu desteklenmiyor: {task}")

    def start_kamikaze_transit(self, plan: KamikazeApproachPlan, *, source: str = "ui") -> OrinCommandReceipt:
        return self._unsupported("kamikaze")

    def start_hss_transit(self, destination: Point, *, source: str = "ui") -> OrinCommandReceipt:
        return self._unsupported("hss")

    def pause_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unsupported("pause")

    def resume_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unsupported("resume")

    def stop_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unsupported("stop")

    def cancel_task(self, task: str = "") -> OrinCommandReceipt:
        return self._unsupported("cancel")

    def set_video_recording(self, enabled: bool) -> OrinCommandReceipt:
        return self._unsupported("video")

    def refresh(self) -> OrinCommandReceipt:
        try:
            packet = self._peer.probe()
            return OrinCommandReceipt(packet.packet_id, True, "İKA bağlantı sorgusu gönderildi")
        except Exception as exc:
            return OrinCommandReceipt(0, False, str(exc))

    def mission_status(self, *, mission_id: int | None = None, task: str | None = None) -> OrinCommandReceipt:
        return self._unsupported("status")

    def mission_events(self, mission_id: int, *, after_sequence: int | None = None, limit: int = 200) -> OrinCommandReceipt:
        return self._unsupported("events")

    def start_chain(self, chain: str) -> OrinCommandReceipt:
        return self._unsupported(chain)


__all__ = ["IkaOnboardMissionService"]


def _plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    def convert(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): convert(nested) for key, nested in item.items()}
        if isinstance(item, (tuple, list)):
            return [convert(nested) for nested in item]
        return item

    return convert(value)
