"""Reusable core independent from UI and product details."""

from src.core.abstracts import Model, Service
from src.core.dependency import (
    BaseDependencyContainer,
    DependencyContainer,
    Inject,
    Lifetime,
    injection,
)
from src.core.events import EventStream, Subscription
from src.core.mission import (
    Mission,
    MissionChain,
    MissionChainSnapshot,
    MissionConflictPolicy,
    MissionControl,
    MissionDescriptor,
    MissionEvent,
    MissionEventLevel,
    MissionEventQuery,
    MissionEventType,
    MissionManagerSnapshot,
    MissionPhase,
    MissionPrerequisitePolicy,
    MissionPriority,
    MissionSnapshot,
)

__all__ = [
    "BaseDependencyContainer",
    "DependencyContainer",
    "EventStream",
    "Inject",
    "Lifetime",
    "Mission",
    "MissionChain",
    "MissionChainSnapshot",
    "MissionConflictPolicy",
    "MissionControl",
    "MissionDescriptor",
    "MissionEvent",
    "MissionEventLevel",
    "MissionEventQuery",
    "MissionEventType",
    "MissionManagerSnapshot",
    "MissionPhase",
    "MissionPrerequisitePolicy",
    "MissionPriority",
    "MissionSnapshot",
    "Model",
    "Service",
    "Subscription",
    "injection",
]
