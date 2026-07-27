from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class MapToolSpec:
    key: str
    label_key: str
    group: Literal["primary", "transfer"] = "primary"
    geometry: Literal["point", "polygon", "action"] = "action"
    route_item: bool = False
    editable: bool = False
    creatable: bool = True


MAP_TOOLS = (
    MapToolSpec("pan", "map.tool.pan"),
    MapToolSpec("takeoff", "map.tool.takeoff", geometry="point", route_item=True, editable=True),
    MapToolSpec("waypoint", "map.tool.waypoint", geometry="point", route_item=True, editable=True),
    MapToolSpec("loiter", "map.tool.loiter", geometry="point", route_item=True, editable=True),
    MapToolSpec("rtl", "map.tool.rtl", geometry="point", route_item=True, editable=True),
    MapToolSpec("no_fly", "map.tool.no_fly", geometry="polygon", editable=True),
    MapToolSpec("safe_zone", "map.tool.safe_zone", geometry="polygon", editable=True),
    # HSS and QR targets come from the TEKNOFEST service. The map renders them,
    # but the operator cannot create or edit them.
    MapToolSpec("hss", "map.tool.hss", geometry="point", creatable=False),
    MapToolSpec("target", "map.tool.target", geometry="point", creatable=False),
    MapToolSpec("land", "map.tool.land", geometry="point", route_item=True, editable=True),
    MapToolSpec("mission_list", "map.tool.mission_list"),
    MapToolSpec("clear", "map.tool.clear"),
    MapToolSpec("archive", "map.tool.archive", group="transfer"),
    MapToolSpec("send", "map.tool.send", group="transfer"),
)

TOOL_LABEL_KEYS = {tool.key: tool.label_key for tool in MAP_TOOLS}


def map_tool_config() -> dict:
    return {
        "primary": [
            tool.key for tool in MAP_TOOLS if tool.group == "primary" and tool.creatable
        ],
        "transfer": [tool.key for tool in MAP_TOOLS if tool.group == "transfer"],
        "missionTypes": [
            {
                "key": tool.key,
                "geometry": tool.geometry,
                "route": tool.route_item,
                "editable": tool.editable,
                "creatable": tool.creatable,
            }
            for tool in MAP_TOOLS
            if tool.geometry != "action"
        ],
    }
