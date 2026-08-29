from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from src.core.events import EventBus
from src.models.competition import CompetitorTelemetry
from src.models.telemetry import TelemetrySnapshot
from src.services.target_selection import TargetSelectionService


def load_tests(_loader, _tests, _pattern):
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value, description=name))
    return suite


class _Competition:
    def __init__(self, competitors: tuple[CompetitorTelemetry, ...]) -> None:
        self.competitors = competitors
        self.competitors_updated = EventBus[tuple[CompetitorTelemetry, ...]]()


class _Telemetry:
    def __init__(self, current: TelemetrySnapshot) -> None:
        self.current = current


def _offset_lat_lon(
    latitude: float,
    longitude: float,
    north_m: float,
    east_m: float,
) -> tuple[float, float]:
    radius = 6_371_000.0
    lat = latitude + math.degrees(north_m / radius)
    lon = longitude + math.degrees(
        east_m / (radius * math.cos(math.radians(latitude)))
    )
    return lat, lon


def _contact(
    team: int,
    *,
    now: datetime,
    north_m: float,
    east_m: float,
    heading_deg: float,
    speed_m_s: float = 23.0,
    altitude_m: float = 100.0,
    age_ms: int = 100,
) -> CompetitorTelemetry:
    lat, lon = _offset_lat_lon(39.0, 32.0, north_m, east_m)
    return CompetitorTelemetry(
        team_number=team,
        latitude=lat,
        longitude=lon,
        altitude_m=altitude_m,
        pitch=0.0,
        heading=heading_deg,
        roll=0.0,
        speed_m_s=speed_m_s,
        data_age_ms=age_ms,
        received_at=now,
        raw={"takim_numarasi": team},
    )


def _ownship(now: datetime) -> TelemetrySnapshot:
    return TelemetrySnapshot.zero().copy(
        team_id=1,
        latitude=39.0,
        longitude=32.0,
        altitude_agl=100.0,
        altitude_msl=100.0,
        ground_speed=25.0,
        air_speed=25.0,
        heading=0.0,
        position_valid=True,
        telemetry_link=True,
        autopilot_link=True,
        position_received_at=now,
        timestamp=now,
    )


def test_stale_targets_are_not_ranked() -> None:
    now = datetime.now(UTC)
    fresh = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0, age_ms=100)
    stale = _contact(3, now=now, north_m=120, east_m=10, heading_deg=0, age_ms=5000)
    service = TargetSelectionService(
        _Competition((fresh, stale)),  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
    )

    result = service.rank_targets()

    assert result.considered_count == 2
    assert result.valid_count == 1
    assert [candidate.team_number for candidate in result.candidates] == [2]


def test_aligned_tail_intercept_outranks_head_on_target() -> None:
    now = datetime.now(UTC)
    aligned = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0)
    head_on = _contact(3, now=now, north_m=140, east_m=0, heading_deg=180)
    service = TargetSelectionService(
        _Competition((aligned, head_on)),  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
    )

    result = service.rank_targets()
    scores = {candidate.team_number: candidate for candidate in result.candidates}

    assert scores[2].final_score > scores[3].final_score
    assert scores[2].lock_probability > scores[3].lock_probability
    assert scores[2].turn_required_deg < scores[3].turn_required_deg
    assert scores[2].best_lock_start_s is not None


def test_simulation_produces_ranked_non_binary_probabilities_and_is_longer_than_lock() -> None:
    now = datetime.now(UTC)
    competitors = (
        _contact(2, now=now, north_m=140, east_m=0, heading_deg=0),
        _contact(4, now=now, north_m=200, east_m=80, heading_deg=15),
        _contact(5, now=now, north_m=120, east_m=-100, heading_deg=310),
    )
    service = TargetSelectionService(
        _Competition(competitors),  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
    )

    result = service.rank_targets()

    assert result.simulated_count == 3
    assert len(result.candidates) == 3
    assert any(0.0 < item.lock_probability < 100.0 for item in result.candidates)
    assert [item.final_score for item in result.candidates] == sorted(
        (item.final_score for item in result.candidates), reverse=True
    )


def test_background_worker_publishes_top5_but_never_selects_a_team() -> None:
    import time

    now = datetime.now(UTC)
    competition = _Competition(
        (
            _contact(2, now=now, north_m=140, east_m=0, heading_deg=0),
            _contact(3, now=now, north_m=170, east_m=25, heading_deg=15),
        )
    )
    service = TargetSelectionService(
        competition,  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
    )
    observed = []
    sub = service.updated.subscribe(observed.append)
    service.start()
    service.set_ranking_enabled(True)
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not observed:
            time.sleep(0.01)
        assert observed
        assert observed[-1].candidates
        # The ranking model has no "selected team" field by design. Selection
        # belongs to the operator/onboard transport service.
        assert not hasattr(observed[-1], "selected_team_number")
    finally:
        service.stop()
        sub.cancel()


class _Orin:
    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.sent: list[tuple[str, dict]] = []
        self.timeouts: list[float | None] = []

    def issue(self, packet_type: str, payload: dict | None = None, *, timeout=None):
        self.sent.append((packet_type, dict(payload or {})))
        self.timeouts.append(timeout)
        return SimpleNamespace(packet_type=packet_type, payload=payload or {})


def test_operator_selection_locks_top5_and_streams_selected_team_only() -> None:
    now = datetime.now(UTC)
    team2 = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0)
    team3 = _contact(3, now=now, north_m=170, east_m=25, heading_deg=15)
    competition = _Competition((team2, team3))
    orin = _Orin()
    service = TargetSelectionService(
        competition,  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
        orin=orin,  # type: ignore[arg-type]
    )
    service.rank_targets()
    service.start()

    service.select_target(2)

    assert service.locked is True
    assert service.selected_team_number == 2
    assert orin.sent[-1][0] == "apn.target.telemetry"
    assert orin.sent[-1][1]["takim_numarasi"] == 2
    assert orin.timeouts[-1] == 0.75

    later = datetime.now(UTC)
    team2_new = _contact(2, now=later, north_m=155, east_m=4, heading_deg=5, age_ms=80)
    team3_new = _contact(3, now=later, north_m=165, east_m=20, heading_deg=20, age_ms=80)
    competition.competitors = (team2_new, team3_new)
    competition.competitors_updated.publish(competition.competitors)

    assert orin.sent[-1][0] == "apn.target.telemetry"
    assert orin.sent[-1][1]["takim_numarasi"] == 2
    assert orin.sent[-1][1]["iha_enlem"] == team2_new.latitude
    assert all(payload["takim_numarasi"] == 2 for _, payload in orin.sent)
    service.stop()


def test_selected_target_does_not_auto_switch_when_it_disappears() -> None:
    now = datetime.now(UTC)
    team2 = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0)
    team3 = _contact(3, now=now, north_m=170, east_m=25, heading_deg=15)
    competition = _Competition((team2, team3))
    orin = _Orin()
    service = TargetSelectionService(
        competition,  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
        orin=orin,  # type: ignore[arg-type]
    )
    service.rank_targets()
    service.start()
    service.select_target(2)
    count = len(orin.sent)

    competition.competitors = (team3,)
    competition.competitors_updated.publish(competition.competitors)

    assert service.locked is True
    assert service.selected_team_number == 2
    assert len(orin.sent) == count + 1
    assert orin.sent[-1][1]["takim_numarasi"] == 2
    assert orin.sent[-1][1]["hedef_gecerli"] is False
    service.stop()


def test_clear_target_unlocks_ranking_without_changing_ui_contract() -> None:
    now = datetime.now(UTC)
    team2 = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0)
    competition = _Competition((team2,))
    service = TargetSelectionService(
        competition,  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
    )
    service.rank_targets()
    service.select_target(2)
    assert service.locked is True

    service.clear_target()

    assert service.locked is False
    assert service.selected_team_number is None


def test_clear_target_invalidates_onboard_target_immediately() -> None:
    now = datetime.now(UTC)
    team2 = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0)
    competition = _Competition((team2,))
    orin = _Orin()
    service = TargetSelectionService(
        competition,  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
        orin=orin,  # type: ignore[arg-type]
    )
    service.rank_targets()
    service.select_target(2)

    service.clear_target()

    assert orin.sent[-1][0] == "apn.target.telemetry"
    assert orin.sent[-1][1]["hedef_gecerli"] is False


def test_manual_rank_call_cannot_change_frozen_top5() -> None:
    now = datetime.now(UTC)
    team2 = _contact(2, now=now, north_m=140, east_m=0, heading_deg=0)
    team3 = _contact(3, now=now, north_m=180, east_m=20, heading_deg=20)
    competition = _Competition((team2, team3))
    service = TargetSelectionService(
        competition,  # type: ignore[arg-type]
        _Telemetry(_ownship(now)),  # type: ignore[arg-type]
    )
    frozen = service.rank_targets()
    service.select_target(frozen.candidates[0].team_number)

    competition.competitors = (
        _contact(4, now=datetime.now(UTC), north_m=100, east_m=0, heading_deg=0),
    )
    after = service.rank_targets()

    assert after == frozen
    assert service.last_result == frozen
