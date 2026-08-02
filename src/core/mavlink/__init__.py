"""Reusable MAVLink infrastructure for ArduPilot-based vehicles."""

from src.core.mavlink.cache import MessageCache
from src.core.mavlink.channel import MavlinkAsyncChannel
from src.core.mavlink.application import (
    DEFAULT_APPLICATION_MESSAGE_TYPE,
    DEFAULT_APPLICATION_NETWORK,
    MavlinkApplicationAssembler,
    MavlinkApplicationChannel,
    MavlinkApplicationCodec,
    MavlinkApplicationPacket,
    MavlinkApplicationProtocolError,
)
from src.core.mavlink.client import MavlinkClient
from src.core.mavlink.connection import MavlinkConnection, MavlinkUnavailableError
from src.core.mavlink.dispatch import (
    MavlinkApplicationDispatch,
    MavlinkApplicationDispatcher,
    MavlinkApplicationHandler,
    MavlinkApplicationHandlerRegistry,
    MavlinkApplicationResult,
)
from src.core.mavlink.endpoint import MavlinkEndpoint
from src.core.mavlink.filter import MavlinkMessageFilter
from src.core.mavlink.message import MavlinkMessageEnvelope
from src.core.mavlink.protocols import MavlinkHeader, MavlinkMessage
from src.core.mavlink.peer import (
    MavlinkApplicationPeer,
    MavlinkApplicationPeerState,
    MavlinkApplicationResponse,
)
from src.core.mavlink.remote_log import (
    MavlinkRemoteLogBatch,
    MavlinkRemoteLogLevel,
    MavlinkRemoteLogRecord,
    REMOTE_LOG_MAX_BATCH_BYTES,
    REMOTE_LOG_MAX_BATCH_RECORDS,
    REMOTE_LOG_MAX_DETAILS_BYTES,
    REMOTE_LOG_PACKET_TYPE,
    REMOTE_LOG_PROTOCOL_VERSION,
)
from src.core.mavlink.router import MavlinkMessageRouter, MavlinkRouterError, MavlinkRouterStats

__all__ = [
    "MavlinkAsyncChannel",
    "MavlinkApplicationAssembler",
    "MavlinkApplicationChannel",
    "MavlinkApplicationCodec",
    "MavlinkApplicationDispatch",
    "MavlinkApplicationDispatcher",
    "MavlinkApplicationHandler",
    "MavlinkApplicationHandlerRegistry",
    "MavlinkApplicationPacket",
    "MavlinkApplicationPeer",
    "MavlinkApplicationPeerState",
    "MavlinkApplicationProtocolError",
    "MavlinkApplicationResponse",
    "MavlinkApplicationResult",
    "MavlinkRemoteLogBatch",
    "MavlinkRemoteLogLevel",
    "MavlinkRemoteLogRecord",
    "DEFAULT_APPLICATION_MESSAGE_TYPE",
    "DEFAULT_APPLICATION_NETWORK",
    "MavlinkClient",
    "MavlinkConnection",
    "MavlinkEndpoint",
    "MavlinkUnavailableError",
    "MavlinkMessageRouter",
    "MavlinkMessageEnvelope",
    "MavlinkMessageFilter",
    "MavlinkHeader",
    "MavlinkMessage",
    "MavlinkRouterError",
    "MavlinkRouterStats",
    "MessageCache",
    "REMOTE_LOG_MAX_BATCH_BYTES",
    "REMOTE_LOG_MAX_BATCH_RECORDS",
    "REMOTE_LOG_MAX_DETAILS_BYTES",
    "REMOTE_LOG_PACKET_TYPE",
    "REMOTE_LOG_PROTOCOL_VERSION",
]
