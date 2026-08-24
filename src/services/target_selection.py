from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import threading
import time
from typing import Iterable

from src.core.events import EventBus, Subscription
from src.communication.contracts import ApnTargetTelemetry
from src.models.competition import CompetitorTelemetry
from src.models.target_selection import (
    TargetCandidateScore,
    TargetSelectionConfig,
    TargetSelectionResult,
)
from src.models.telemetry import TelemetrySnapshot
from src.services.abstracts.competition import CompetitionService
from src.services.abstracts.telemetry import TelemetryService
from src.services.abstracts.orin import OrinService
from src.services.logging_service import ServiceLogger


_G = 9.80665
_EARTH_RADIUS_M = 6_371_000.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _angle_diff_deg(target: float, current: float) -> float:
    return (float(target) - float(current) + 180.0) % 360.0 - 180.0


def _heading_vector(heading_deg: float) -> tuple[float, float]:
    radians = math.radians(heading_deg)
    return math.cos(radians), math.sin(radians)  # north, east


def _bearing_deg(north: float, east: float) -> float:
    return math.degrees(math.atan2(east, north)) % 360.0


def _sigmoid(value: float) -> float:
    if value >= 40.0:
        return 1.0
    if value <= -40.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _gaussian(value: float, sigma: float) -> float:
    sigma = max(1e-6, sigma)
    return math.exp(-0.5 * (value / sigma) ** 2)


def _to_local_ne(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    lat0 = math.radians(ref_lat)
    dlat = math.radians(lat - ref_lat)
    dlon = math.radians(lon - ref_lon)
    north = dlat * _EARTH_RADIUS_M
    east = dlon * _EARTH_RADIUS_M * max(0.01, math.cos(lat0))
    return north, east


def _sample_time(contact: CompetitorTelemetry) -> datetime:
    return contact.received_at.astimezone(UTC) - timedelta(milliseconds=contact.data_age_ms)


def _effective_age_s(contact: CompetitorTelemetry, now: datetime) -> float:
    return max(0.0, (now - _sample_time(contact)).total_seconds())


@dataclass(slots=True)
class _TargetKinematics:
    contact: CompetitorTelemetry
    north_m: float
    east_m: float
    altitude_m: float
    speed_m_s: float
    heading_deg: float
    turn_rate_deg_s: float
    accel_m_s2: float
    climb_m_s: float
    age_s: float
    data_confidence: float


@dataclass(slots=True)
class _OwnshipState:
    north_m: float
    east_m: float
    altitude_m: float
    speed_m_s: float
    heading_deg: float
    climb_m_s: float


@dataclass(slots=True)
class _PreCandidate:
    state: _TargetKinematics
    pre_score: float
    tail_access_score: float
    distance_m: float
    altitude_delta_m: float
    merge_eta_s: float
    heading_alignment_deg: float
    turn_required_deg: float


@dataclass(frozen=True, slots=True)
class _Scenario:
    turn_offset_deg_s: float
    speed_offset_m_s: float
    climb_offset_m_s: float
    weight: float


_SCENARIOS = (
    _Scenario(0.0, 0.0, 0.0, 0.28),
    _Scenario(999.0, 0.0, 0.0, 0.16),  # 999 => force straight flight
    _Scenario(-4.0, 0.0, 0.0, 0.12),
    _Scenario(+4.0, 0.0, 0.0, 0.12),
    _Scenario(0.0, -2.5, 0.0, 0.08),
    _Scenario(0.0, +2.5, 0.0, 0.08),
    _Scenario(0.0, 0.0, -2.0, 0.05),
    _Scenario(0.0, 0.0, +2.0, 0.05),
    _Scenario(-999.0, 0.0, 0.0, 0.06),  # reverse the observed turn trend
)


class TargetSelectionService:
    """GCS-side target ranking engine.

    It keeps a short per-team telemetry history, time-normalizes every state to
    one reference instant, performs a fast tail-access pre-score, then runs a
    deterministic multi-scenario point-mass simulation for the best candidates.
    """

    def __init__(
        self,
        competition: CompetitionService,
        telemetry: TelemetryService,
        logger: ServiceLogger | None = None,
        config: TargetSelectionConfig | None = None,
        orin: OrinService | None = None,
    ) -> None:
        self._competition = competition
        self._telemetry = telemetry
        self._logger = logger
        self._orin = orin
        self._config = config or TargetSelectionConfig()
        self._histories: dict[int, deque[CompetitorTelemetry]] = defaultdict(
            lambda: deque(maxlen=self._config.history_limit)
        )
        self._lock = threading.RLock()
        self._subscription: Subscription | None = None
        self._updated = EventBus[TargetSelectionResult]()
        self._selection_changed = EventBus[int | None]()
        self._selected_telemetry_updated = EventBus[CompetitorTelemetry | None]()
        self._rank_stop = threading.Event()
        self._rank_wakeup = threading.Event()
        self._rank_thread: threading.Thread | None = None
        self._started = False
        self._last_result = TargetSelectionResult()
        self._locked = False
        self._selected_team_number: int | None = None
        self._selected_target: CompetitorTelemetry | None = None
        self._selected_target_available: bool | None = None
        self._apn_transport_state = ""

    @property
    def config(self) -> TargetSelectionConfig:
        return self._config

    @property
    def last_result(self) -> TargetSelectionResult:
        with self._lock:
            return self._last_result

    @property
    def updated(self) -> EventBus[TargetSelectionResult]:
        """Publishes Top-5 updates while target selection is unlocked."""
        return self._updated

    @property
    def selection_changed(self) -> EventBus[int | None]:
        """Publishes the operator-selected team number, or ``None`` on clear."""
        return self._selection_changed

    @property
    def selected_telemetry_updated(self) -> EventBus[CompetitorTelemetry | None]:
        """Publishes fresh telemetry for the locked team without changing selection."""
        return self._selected_telemetry_updated

    @property
    def locked(self) -> bool:
        with self._lock:
            return self._locked

    @property
    def selected_team_number(self) -> int | None:
        with self._lock:
            return self._selected_team_number

    @property
    def selected_target(self) -> CompetitorTelemetry | None:
        """Latest complete TEKNOFEST row for the locked target, including ``raw``."""
        with self._lock:
            return self._selected_target

    def team_history(self, team_number: int) -> tuple[CompetitorTelemetry, ...]:
        with self._lock:
            return tuple(self._histories.get(int(team_number), ()))

    def histories_snapshot(self) -> dict[int, tuple[CompetitorTelemetry, ...]]:
        with self._lock:
            return {team: tuple(items) for team, items in self._histories.items()}

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self._subscription = self._competition.competitors_updated.subscribe(
            self._on_competitors_updated
        )
        self._capture_competitors(self._competition.competitors)
        self._rank_stop.clear()
        self._rank_wakeup.clear()
        self._rank_thread = threading.Thread(
            target=self._rank_loop,
            name="TargetSelectionWorker",
            daemon=True,
        )
        self._rank_thread.start()
        self._rank_wakeup.set()

    def stop(self) -> None:
        subscription = self._subscription
        self._subscription = None
        if subscription is not None:
            subscription.cancel()
        self._rank_stop.set()
        self._rank_wakeup.set()
        thread = self._rank_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._config.refresh_interval_s + 0.5))
        self._rank_thread = None
        with self._lock:
            self._started = False

    def select_target(self, team_number: int) -> CompetitorTelemetry:
        """Lock the current Top-5 and start streaming one team to onboard/APN.

        Ranking stays frozen until :meth:`clear_target` is called. The selected
        team's telemetry continues to refresh from TEKNOFEST and is forwarded
        through the shared ``apn.target.telemetry`` contract.
        """
        team_number = int(team_number)
        if team_number <= 0:
            raise ValueError("Hedef takım numarası pozitif olmalı")
        with self._lock:
            allowed = {item.team_number for item in self._last_result.candidates}
            if team_number not in allowed:
                raise ValueError("Hedef yalnızca güncel Top-5 listesinden seçilebilir")
        target = next(
            (item for item in self._competition.competitors if item.team_number == team_number),
            None,
        )
        if target is None:
            raise RuntimeError(f"Takım {team_number} için güncel TEKNOFEST telemetrisi yok")
        if self._current_age_ms(target) > self._config.selected_target_max_age_ms:
            raise RuntimeError(
                f"Takım {team_number} telemetrisi eski ({self._current_age_ms(target)} ms)"
            )
        with self._lock:
            self._locked = True
            self._selected_team_number = team_number
            self._selected_target = target
            self._selected_target_available = True
        self._selection_changed.publish(team_number)
        self._selected_telemetry_updated.publish(target)
        self._forward_to_apn(target)
        self._log(
            "target_selection.locked",
            f"Top-5 kilitlendi; aktif APN hedefi Takım {team_number}",
            "success",
            {"team_number": team_number},
        )
        return target

    def clear_target(self) -> None:
        with self._lock:
            previous = self._selected_team_number
            previous_target = self._selected_target
            self._locked = False
            self._selected_team_number = None
            self._selected_target = None
            self._selected_target_available = None
        if previous_target is not None:
            self._forward_to_apn(previous_target, active=False)
        self._selection_changed.publish(None)
        self._selected_telemetry_updated.publish(None)
        self._rank_wakeup.set()
        self._log(
            "target_selection.unlocked",
            "Aktif APN hedefi temizlendi; Top-5 güncellemesi yeniden açıldı",
            "info",
            {"previous_team_number": previous},
        )

    def _on_competitors_updated(
        self,
        contacts: tuple[CompetitorTelemetry, ...],
    ) -> None:
        self._capture_competitors(contacts)
        with self._lock:
            team_number = self._selected_team_number if self._locked else None
        if team_number is None:
            return
        target = next((item for item in contacts if item.team_number == team_number), None)
        if target is None or self._current_age_ms(target) > self._config.selected_target_max_age_ms:
            with self._lock:
                already_unavailable = self._selected_target_available is False
                self._selected_target_available = False
            if not already_unavailable:
                stale_target = self.selected_target
                if stale_target is not None:
                    self._forward_to_apn(stale_target, active=False)
                self._selected_telemetry_updated.publish(None)
                self._log(
                    "target_selection.selected_target_lost",
                    f"Takım {team_number} telemetrisi kayıp/eski; hedef değiştirilmedi",
                    "warning",
                    {"team_number": team_number},
                )
            return
        with self._lock:
            was_unavailable = self._selected_target_available is False
            self._selected_target = target
            self._selected_target_available = True
        if was_unavailable:
            self._log(
                "target_selection.selected_target_recovered",
                f"Takım {team_number} telemetrisi yeniden geldi",
                "success",
                {"team_number": team_number},
            )
        self._selected_telemetry_updated.publish(target)
        self._forward_to_apn(target)

    def _rank_loop(self) -> None:
        """Refresh Top-5 at a fixed two-second cadence until operator lock."""
        immediate = True
        while not self._rank_stop.is_set():
            if not immediate:
                self._rank_wakeup.wait(timeout=self._config.refresh_interval_s)
            immediate = False
            self._rank_wakeup.clear()
            if self._rank_stop.is_set():
                return
            with self._lock:
                locked = self._locked
            if locked:
                self._check_selected_freshness()
                continue
            try:
                result = self.rank_targets()
            except Exception as exc:
                self._log(
                    "target_selection.background_failed",
                    f"Hedef sıralaması güncellenemedi: {exc}",
                    "warning",
                    {"error": str(exc)},
                )
                continue
            self._updated.publish(result)

    def _check_selected_freshness(self) -> None:
        """Detect a stalled TEKNOFEST stream without changing the locked team."""
        with self._lock:
            team_number = self._selected_team_number
            target = self._selected_target
            was_available = self._selected_target_available
        if team_number is None or target is None:
            return
        if self._current_age_ms(target) <= self._config.selected_target_max_age_ms:
            return
        with self._lock:
            self._selected_target_available = False
        if was_available is not False:
            self._forward_to_apn(target, active=False)
            self._selected_telemetry_updated.publish(None)
            self._log(
                "target_selection.selected_target_stale",
                f"Takım {team_number} telemetrisi zaman aşımına uğradı; hedef kilidi korunuyor",
                "warning",
                {"team_number": team_number},
            )

    def _forward_to_apn(
        self,
        target: CompetitorTelemetry,
        *,
        active: bool = True,
    ) -> bool:
        contract = ApnTargetTelemetry(
            team_number=int(target.team_number),
            latitude_deg=float(target.latitude),
            longitude_deg=float(target.longitude),
            altitude_m=float(target.altitude_m),
            pitch_deg=float(target.pitch),
            heading_deg=float(target.heading) % 360.0,
            roll_deg=float(target.roll),
            speed_m_s=float(target.speed_m_s),
            time_difference_ms=int(target.data_age_ms),
            active=bool(active),
        )
        orin = self._orin
        if orin is None or not orin.connected:
            if self._apn_transport_state != "disconnected":
                self._log(
                    "target_selection.apn_stream_unavailable",
                    f"Takım {target.team_number} seçili; Orin bağlı değil, APN telemetrisi bekletiliyor",
                    "warning",
                    {"team_number": target.team_number},
                )
            self._apn_transport_state = "disconnected"
            return False
        try:
            # APN target telemetry is a shared REQUEST contract.  Using issue()
            # keeps the GCS transport and onboard ApplicationMessageService on
            # the same exchange kind and gives us a correlated acceptance ACK.
            receipt = orin.issue(
                contract.packet_type,
                contract.to_payload(),
                timeout=0.75,
            )
            if getattr(receipt, "accepted", True) is not True:
                raise RuntimeError(
                    str(getattr(receipt, "message", "Onboard APN hedefini reddetti"))
                )
        except Exception as exc:
            state = f"error:{type(exc).__name__}:{exc}"
            if self._apn_transport_state != state:
                self._log(
                    "target_selection.apn_stream_failed",
                    f"Takım {target.team_number} APN telemetrisi gönderilemedi: {exc}",
                    "warning",
                    {"team_number": target.team_number, "error": str(exc)},
                )
            self._apn_transport_state = state
            return False
        if self._apn_transport_state:
            self._log(
                "target_selection.apn_stream_recovered",
                f"Takım {target.team_number} APN telemetri akışı aktif",
                "success",
                {"team_number": target.team_number},
            )
        self._apn_transport_state = ""
        return True

    @staticmethod
    def _current_age_ms(target: CompetitorTelemetry) -> int:
        sample_time = _sample_time(target)
        return max(0, int((datetime.now(UTC) - sample_time).total_seconds() * 1000.0))

    def _log(
        self,
        action: str,
        message: str,
        severity: str,
        details: dict | None = None,
    ) -> None:
        if self._logger is not None:
            self._logger.event(
                action,
                message,
                severity=severity,
                details=details or {},
            )

    def rank_targets(self) -> TargetSelectionResult:
        with self._lock:
            if self._locked:
                return self._last_result
        started = time.perf_counter()
        now = datetime.now(UTC)
        contacts = self._competition.competitors
        self._capture_competitors(contacts)
        own_snapshot = self._telemetry.current
        own, ref_lat, ref_lon = self._ownship_at_reference(own_snapshot, now)

        pre_candidates: list[_PreCandidate] = []
        for contact in contacts:
            target = self._target_at_reference(contact, now, ref_lat, ref_lon)
            if target is None:
                continue
            pre_candidates.append(self._pre_score(own, target))

        pre_candidates.sort(key=lambda item: item.pre_score, reverse=True)
        simulated = pre_candidates[: self._config.prefilter_limit]
        finals = [self._simulate_candidate(own, candidate) for candidate in simulated]
        finals.sort(key=lambda item: item.final_score, reverse=True)
        finalists = tuple(finals[: self._config.final_limit])
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result = TargetSelectionResult(
            candidates=finalists,
            considered_count=len(contacts),
            valid_count=len(pre_candidates),
            simulated_count=len(simulated),
            elapsed_ms=elapsed_ms,
            reference_time_utc=now.isoformat().replace("+00:00", "Z"),
        )
        with self._lock:
            self._last_result = result
        if self._logger:
            self._logger.event(
                "target_selection.completed",
                f"Hedef seçimi tamamlandı: {len(contacts)} rakip, {len(simulated)} simülasyon, {len(finalists)} finalist",
                severity="success",
                details={
                    "considered": len(contacts),
                    "valid": len(pre_candidates),
                    "simulated": len(simulated),
                    "finalists": [item.team_number for item in finalists],
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        return result

    def _capture_competitors(self, contacts: Iterable[CompetitorTelemetry]) -> None:
        with self._lock:
            for contact in contacts:
                history = self._histories[contact.team_number]
                source_time = _sample_time(contact)
                if history:
                    previous_time = _sample_time(history[-1])
                    if abs((source_time - previous_time).total_seconds()) < 0.02:
                        if contact.received_at >= history[-1].received_at:
                            history[-1] = contact
                        continue
                history.append(contact)

    def _ownship_at_reference(
        self,
        snapshot: TelemetrySnapshot,
        now: datetime,
    ) -> tuple[_OwnshipState, float, float]:
        if not snapshot.has_valid_position:
            raise RuntimeError("Hedef seçimi için geçerli kendi İHA konumu yok")
        received_at = snapshot.position_received_at or snapshot.timestamp
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        own_age_s = max(0.0, (now - received_at.astimezone(UTC)).total_seconds())
        if own_age_s * 1000.0 > self._config.max_ownship_age_ms:
            raise RuntimeError(
                f"Kendi İHA telemetrisi eski ({own_age_s * 1000.0:.0f} ms)"
            )
        speed = max(0.0, float(snapshot.ground_speed))
        north_u, east_u = _heading_vector(snapshot.heading)
        own = _OwnshipState(
            north_m=north_u * speed * own_age_s,
            east_m=east_u * speed * own_age_s,
            altitude_m=float(snapshot.altitude_agl) + float(snapshot.climb_rate) * own_age_s,
            speed_m_s=max(self._config.own_min_speed_m_s, speed),
            heading_deg=float(snapshot.heading) % 360.0,
            climb_m_s=float(snapshot.climb_rate),
        )
        return own, float(snapshot.latitude), float(snapshot.longitude)

    def _target_at_reference(
        self,
        contact: CompetitorTelemetry,
        now: datetime,
        ref_lat: float,
        ref_lon: float,
    ) -> _TargetKinematics | None:
        age_s = _effective_age_s(contact, now)
        if age_s * 1000.0 > self._config.max_target_age_ms:
            return None
        north, east = _to_local_ne(contact.latitude, contact.longitude, ref_lat, ref_lon)
        turn_rate, accel, climb = self._estimate_motion(contact.team_number)
        turn_rate = _clamp(turn_rate, -20.0, 20.0)
        accel = _clamp(accel, -5.0, 5.0)
        climb = _clamp(climb, -12.0, 12.0)
        north, east, heading, speed, altitude = self._propagate_target(
            north,
            east,
            float(contact.altitude_m),
            max(0.0, float(contact.speed_m_s)),
            float(contact.heading),
            turn_rate,
            accel,
            climb,
            age_s,
        )
        data_confidence = math.exp(-age_s / 1.5)
        return _TargetKinematics(
            contact=contact,
            north_m=north,
            east_m=east,
            altitude_m=altitude,
            speed_m_s=speed,
            heading_deg=heading,
            turn_rate_deg_s=turn_rate,
            accel_m_s2=accel,
            climb_m_s=climb,
            age_s=age_s,
            data_confidence=data_confidence,
        )

    def _estimate_motion(self, team_number: int) -> tuple[float, float, float]:
        with self._lock:
            history = tuple(self._histories.get(team_number, ()))
        if len(history) < 2:
            return 0.0, 0.0, 0.0
        rates: list[tuple[float, float, float, float]] = []
        recent = history[-5:]
        for first, second in zip(recent, recent[1:]):
            dt = (_sample_time(second) - _sample_time(first)).total_seconds()
            if not 0.15 <= dt <= 5.0:
                continue
            heading_rate = _angle_diff_deg(second.heading, first.heading) / dt
            acceleration = (second.speed_m_s - first.speed_m_s) / dt
            climb = (second.altitude_m - first.altitude_m) / dt
            rates.append((heading_rate, acceleration, climb, dt))
        if not rates:
            first, second = history[-2], history[-1]
            dt = (_sample_time(second) - _sample_time(first)).total_seconds()
            if dt <= 0.15:
                return 0.0, 0.0, 0.0
            return (
                _angle_diff_deg(second.heading, first.heading) / dt,
                (second.speed_m_s - first.speed_m_s) / dt,
                (second.altitude_m - first.altitude_m) / dt,
            )
        total_weight = sum(item[3] for item in rates)
        return tuple(
            sum(item[index] * item[3] for item in rates) / total_weight
            for index in range(3)
        )  # type: ignore[return-value]

    @staticmethod
    def _propagate_target(
        north: float,
        east: float,
        altitude: float,
        speed: float,
        heading: float,
        turn_rate_deg_s: float,
        accel_m_s2: float,
        climb_m_s: float,
        dt: float,
    ) -> tuple[float, float, float, float, float]:
        if dt <= 0:
            return north, east, heading % 360.0, speed, altitude
        average_speed = max(0.0, speed + 0.5 * accel_m_s2 * dt)
        turn_rad_s = math.radians(turn_rate_deg_s)
        heading_rad = math.radians(heading)
        if abs(turn_rad_s) < 1e-5:
            north += math.cos(heading_rad) * average_speed * dt
            east += math.sin(heading_rad) * average_speed * dt
        else:
            heading_end = heading_rad + turn_rad_s * dt
            radius = average_speed / turn_rad_s
            north += radius * (math.sin(heading_end) - math.sin(heading_rad))
            east += -radius * (math.cos(heading_end) - math.cos(heading_rad))
        return (
            north,
            east,
            (heading + turn_rate_deg_s * dt) % 360.0,
            max(0.0, speed + accel_m_s2 * dt),
            altitude + climb_m_s * dt,
        )

    def _pre_score(self, own: _OwnshipState, target: _TargetKinematics) -> _PreCandidate:
        dn = target.north_m - own.north_m
        de = target.east_m - own.east_m
        horizontal_distance = math.hypot(dn, de)
        altitude_delta = target.altitude_m - own.altitude_m
        distance_m = math.hypot(horizontal_distance, altitude_delta)

        best_quality = 0.0
        best_eta = self._config.precheck_horizon_s
        best_turn = 180.0
        best_heading_alignment = abs(_angle_diff_deg(target.heading_deg, own.heading_deg))
        t = self._config.precheck_step_s
        while t <= self._config.precheck_horizon_s + 1e-6:
            tn, te, theading, tspeed, _talt = self._propagate_target(
                target.north_m,
                target.east_m,
                target.altitude_m,
                target.speed_m_s,
                target.heading_deg,
                target.turn_rate_deg_s,
                target.accel_m_s2,
                target.climb_m_s,
                t,
            )
            rear_n, rear_e = _heading_vector(theading)
            tail_n = tn - rear_n * self._config.tail_distance_m
            tail_e = te - rear_e * self._config.tail_distance_m
            path_n = tail_n - own.north_m
            path_e = tail_e - own.east_m
            path_distance = math.hypot(path_n, path_e)
            desired_bearing = _bearing_deg(path_n, path_e) if path_distance > 0.5 else own.heading_deg
            turn_required = abs(_angle_diff_deg(desired_bearing, own.heading_deg))
            speed_for_turn = max(self._config.own_min_speed_m_s, own.speed_m_s)
            max_turn_rate = math.degrees(
                _G * math.tan(math.radians(self._config.own_max_bank_deg)) / speed_for_turn
            )
            turn_time = turn_required / max(1.0, max_turn_rate)

            # A fixed-wing aircraft keeps translating while it turns.  Estimate
            # how far our aircraft can physically travel by t while accelerating
            # toward its configured maximum instead of assuming an arbitrary
            # target-speed + 4 m/s intercept speed.
            effective_travel_time = max(0.0, t - 0.35 * turn_time)
            accel = max(0.1, self._config.own_max_accel_m_s2)
            v0 = _clamp(
                own.speed_m_s,
                self._config.own_min_speed_m_s,
                self._config.own_max_speed_m_s,
            )
            accelerate_for = min(
                effective_travel_time,
                max(0.0, (self._config.own_max_speed_m_s - v0) / accel),
            )
            reachable_distance = (
                v0 * accelerate_for
                + 0.5 * accel * accelerate_for * accelerate_for
                + self._config.own_max_speed_m_s
                * max(0.0, effective_travel_time - accelerate_for)
            )
            distance_margin = reachable_distance - path_distance
            distance_scale = max(12.0, 0.55 * max(v0, tspeed))
            turn_feasibility = _sigmoid((t - turn_time) / 0.65)
            distance_feasibility = _sigmoid(distance_margin / distance_scale)
            feasibility = turn_feasibility * distance_feasibility
            arrival_alignment = abs(_angle_diff_deg(theading, desired_bearing))
            alignment_score = _gaussian(arrival_alignment, 34.0)
            time_score = math.exp(-t / 18.0)
            quality = feasibility * (0.48 + 0.52 * alignment_score) * time_score
            if quality > best_quality:
                best_quality = quality
                best_eta = t
                best_turn = turn_required
                best_heading_alignment = arrival_alignment
            t += self._config.precheck_step_s

        tail_access = 100.0 * best_quality
        distance_score = _gaussian(max(0.0, distance_m - 100.0), 420.0)
        altitude_score = _gaussian(altitude_delta, 65.0)
        speed_score = _gaussian(target.speed_m_s - own.speed_m_s, 15.0)
        turn_score = _gaussian(best_turn, 70.0)
        pre = 100.0 * (
            0.38 * best_quality
            + 0.12 * distance_score
            + 0.10 * altitude_score
            + 0.08 * speed_score
            + 0.14 * turn_score
            + 0.18 * target.data_confidence
        )
        return _PreCandidate(
            state=target,
            pre_score=_clamp(pre, 0.0, 100.0),
            tail_access_score=_clamp(tail_access, 0.0, 100.0),
            distance_m=distance_m,
            altitude_delta_m=altitude_delta,
            merge_eta_s=best_eta,
            heading_alignment_deg=best_heading_alignment,
            turn_required_deg=best_turn,
        )

    def _simulate_candidate(
        self,
        own: _OwnshipState,
        candidate: _PreCandidate,
    ) -> TargetCandidateScore:
        weighted_probability = 0.0
        weighted_quality = 0.0
        weighted_best_start = 0.0
        weighted_lock_duration = 0.0
        successful_weight = 0.0
        for scenario in _SCENARIOS:
            probability, quality, best_start, lock_duration = self._simulate_scenario(
                own,
                candidate.state,
                scenario,
            )
            weighted_probability += scenario.weight * probability
            weighted_quality += scenario.weight * quality
            weighted_lock_duration += scenario.weight * lock_duration
            if best_start is not None:
                weighted_best_start += scenario.weight * best_start
                successful_weight += scenario.weight

        lock_probability = _clamp(weighted_probability, 0.0, 1.0)
        sim_quality = _clamp(weighted_quality, 0.0, 1.0)
        acquisition_score = math.exp(-candidate.merge_eta_s / 10.0)
        final = 100.0 * (
            0.64 * lock_probability
            + 0.14 * (candidate.tail_access_score / 100.0)
            + 0.12 * candidate.state.data_confidence
            + 0.10 * acquisition_score
        )
        best_start = (
            weighted_best_start / successful_weight if successful_weight > 1e-6 else None
        )
        reason = self._reason(candidate, lock_probability, best_start)
        return TargetCandidateScore(
            team_number=candidate.state.contact.team_number,
            final_score=round(_clamp(final, 0.0, 100.0), 1),
            lock_probability=round(lock_probability * 100.0, 1),
            pre_score=round(candidate.pre_score, 1),
            tail_access_score=round(candidate.tail_access_score, 1),
            data_confidence=round(candidate.state.data_confidence * 100.0, 1),
            distance_m=round(candidate.distance_m, 1),
            altitude_delta_m=round(candidate.altitude_delta_m, 1),
            data_age_ms=int(round(candidate.state.age_s * 1000.0)),
            merge_eta_s=round(candidate.merge_eta_s, 2),
            best_lock_start_s=None if best_start is None else round(best_start, 2),
            best_lock_duration_s=round(weighted_lock_duration, 2),
            heading_alignment_deg=round(candidate.heading_alignment_deg, 1),
            turn_required_deg=round(candidate.turn_required_deg, 1),
            simulation_quality=round(sim_quality * 100.0, 1),
            reason=reason,
        )

    def _simulate_scenario(
        self,
        own_initial: _OwnshipState,
        target: _TargetKinematics,
        scenario: _Scenario,
    ) -> tuple[float, float, float | None, float]:
        dt = self._config.simulation_dt_s
        steps = int(self._config.simulation_horizon_s / dt)
        window_steps = max(1, int(round(self._config.lock_duration_s / dt)))

        on = own_initial.north_m
        oe = own_initial.east_m
        oa = own_initial.altitude_m
        ospeed = _clamp(
            own_initial.speed_m_s,
            self._config.own_min_speed_m_s,
            self._config.own_max_speed_m_s,
        )
        oheading = own_initial.heading_deg

        tn = target.north_m
        te = target.east_m
        ta = target.altitude_m
        tspeed = max(4.0, target.speed_m_s + scenario.speed_offset_m_s)
        theading = target.heading_deg
        if scenario.turn_offset_deg_s == 999.0:
            turn_rate = 0.0
        elif scenario.turn_offset_deg_s == -999.0:
            turn_rate = -target.turn_rate_deg_s
        else:
            turn_rate = target.turn_rate_deg_s + scenario.turn_offset_deg_s
        turn_rate = _clamp(turn_rate, -22.0, 22.0)
        tclimb = _clamp(target.climb_m_s + scenario.climb_offset_m_s, -12.0, 12.0)

        qualities: list[float] = []
        longest_lock_s = 0.0
        current_lock_s = 0.0
        handoff_hold_s = 0.0
        visual_tracking_active = False
        earliest_lock_start_s: float | None = None
        for step_index in range(steps):
            tn, te, theading, tspeed, ta = self._propagate_target(
                tn,
                te,
                ta,
                tspeed,
                theading,
                turn_rate,
                0.0,
                tclimb,
                dt,
            )

            rear_n, rear_e = _heading_vector(theading)
            tail_n = tn - rear_n * self._config.tail_distance_m
            tail_e = te - rear_e * self._config.tail_distance_m
            guide_n = tail_n - on
            guide_e = tail_e - oe
            tail_distance = math.hypot(guide_n, guide_e)
            desired_heading = (
                _bearing_deg(guide_n, guide_e) if tail_distance > 0.5 else theading
            )
            speed_for_turn = max(self._config.own_min_speed_m_s, ospeed)
            max_turn_rate = math.degrees(
                _G * math.tan(math.radians(self._config.own_max_bank_deg)) / speed_for_turn
            )
            heading_error = _angle_diff_deg(desired_heading, oheading)
            heading_delta = _clamp(heading_error, -max_turn_rate * dt, max_turn_rate * dt)
            oheading = (oheading + heading_delta) % 360.0

            # Along-track error is signed: positive means the moving tail
            # capture point is still ahead, negative means we overshot it.
            # At the capture point desired speed therefore converges to the
            # target speed, which is the condition needed before visual handoff.
            along_track_error = guide_n * rear_n + guide_e * rear_e
            desired_speed = _clamp(
                tspeed + 0.12 * _clamp(along_track_error, -90.0, 90.0),
                self._config.own_min_speed_m_s,
                self._config.own_max_speed_m_s,
            )
            speed_error = desired_speed - ospeed
            accel_limit = (
                self._config.own_max_accel_m_s2
                if speed_error >= 0
                else self._config.own_max_decel_m_s2
            )
            ospeed += _clamp(speed_error, -accel_limit * dt, accel_limit * dt)
            ou_n, ou_e = _heading_vector(oheading)
            on += ou_n * ospeed * dt
            oe += ou_e * ospeed * dt

            altitude_error = ta - oa
            vertical_command = _clamp(
                altitude_error * 0.35,
                -self._config.own_max_descent_m_s,
                self._config.own_max_climb_m_s,
            )
            oa += vertical_command * dt

            # The real architecture is two-stage: APN/path guidance first
            # settles behind the target and approximately matches direction;
            # only then does image-based tracking start.  Require that handoff
            # geometry to remain credible for a short dwell before evaluating
            # the four-second visual lock window.
            handoff_quality = self._handoff_quality(
                on, oe, oa, oheading, tn, te, ta, theading
            )
            if not visual_tracking_active:
                if handoff_quality >= 0.58:
                    handoff_hold_s += dt
                    if handoff_hold_s >= 0.35:
                        visual_tracking_active = True
                else:
                    handoff_hold_s = 0.0

            q = (
                self._lock_quality(
                    on,
                    oe,
                    oa,
                    ospeed,
                    oheading,
                    tn,
                    te,
                    ta,
                    tspeed,
                    theading,
                )
                if visual_tracking_active
                else 0.0
            )
            qualities.append(q)
            if visual_tracking_active and q >= 0.52:
                current_lock_s += dt
                longest_lock_s = max(longest_lock_s, current_lock_s)
                if (
                    earliest_lock_start_s is None
                    and current_lock_s + 1e-9 >= self._config.lock_duration_s
                ):
                    elapsed = (step_index + 1) * dt
                    earliest_lock_start_s = max(
                        0.0, elapsed - self._config.lock_duration_s
                    )
            else:
                current_lock_s = 0.0

        if len(qualities) < window_steps:
            return 0.0, 0.0, None, longest_lock_s

        prefix = [0.0]
        for value in qualities:
            prefix.append(prefix[-1] + value)
        best_avg = -1.0
        best_p10 = 0.0
        best_start_index = 0
        # 50 ms stepping is deliberate here; 16 s x 9 scenarios is still tiny.
        for start in range(0, len(qualities) - window_steps + 1):
            end = start + window_steps
            avg = (prefix[end] - prefix[start]) / window_steps
            if avg <= best_avg:
                continue
            window = qualities[start:end]
            ordered = sorted(window)
            p10 = ordered[max(0, int(0.10 * (len(ordered) - 1)))]
            robustness = 0.72 * avg + 0.28 * p10
            current_best_robustness = 0.72 * best_avg + 0.28 * best_p10
            if robustness > current_best_robustness:
                best_avg = avg
                best_p10 = p10
                best_start_index = start

        if best_avg < 0.0:
            return 0.0, 0.0, None, longest_lock_s
        chance = _sigmoid((best_avg - 0.64) / 0.085) * _sigmoid(
            (best_p10 - 0.42) / 0.11
        )
        best_quality = _clamp(0.72 * best_avg + 0.28 * best_p10, 0.0, 1.0)
        return chance, best_quality, earliest_lock_start_s, longest_lock_s

    def _handoff_quality(
        self,
        on: float,
        oe: float,
        oa: float,
        oheading: float,
        tn: float,
        te: float,
        ta: float,
        theading: float,
    ) -> float:
        rel_n = tn - on
        rel_e = te - oe
        horizontal_range = max(0.01, math.hypot(rel_n, rel_e))
        slant_range = math.hypot(horizontal_range, ta - oa)
        bearing_to_target = _bearing_deg(rel_n, rel_e)
        camera_angle = abs(_angle_diff_deg(bearing_to_target, oheading))
        target_to_own = (bearing_to_target + 180.0) % 360.0
        target_rear = (theading + 180.0) % 360.0
        tail_angle = abs(_angle_diff_deg(target_to_own, target_rear))
        heading_alignment = abs(_angle_diff_deg(theading, oheading))

        camera = _gaussian(camera_angle, 28.0)
        tail = _gaussian(tail_angle, 27.0)
        alignment = _gaussian(heading_alignment, 30.0)
        range_score = _gaussian(
            slant_range - self._config.tail_distance_m,
            72.0,
        )
        altitude = _gaussian(ta - oa, 40.0)
        weighted_log = (
            0.22 * math.log(max(camera, 1e-8))
            + 0.28 * math.log(max(tail, 1e-8))
            + 0.25 * math.log(max(alignment, 1e-8))
            + 0.17 * math.log(max(range_score, 1e-8))
            + 0.08 * math.log(max(altitude, 1e-8))
        )
        return _clamp(math.exp(weighted_log), 0.0, 1.0)

    def _lock_quality(
        self,
        on: float,
        oe: float,
        oa: float,
        ospeed: float,
        oheading: float,
        tn: float,
        te: float,
        ta: float,
        tspeed: float,
        theading: float,
    ) -> float:
        rel_n = tn - on
        rel_e = te - oe
        horizontal_range = max(0.01, math.hypot(rel_n, rel_e))
        altitude_delta = ta - oa
        slant_range = math.hypot(horizontal_range, altitude_delta)
        bearing_to_target = _bearing_deg(rel_n, rel_e)
        los_angle = abs(_angle_diff_deg(bearing_to_target, oheading))
        bearing_target_to_own = (bearing_to_target + 180.0) % 360.0
        rear_heading = (theading + 180.0) % 360.0
        tail_angle = abs(_angle_diff_deg(bearing_target_to_own, rear_heading))
        heading_alignment = abs(_angle_diff_deg(theading, oheading))

        own_n, own_e = _heading_vector(oheading)
        target_n, target_e = _heading_vector(theading)
        los_n = rel_n / horizontal_range
        los_e = rel_e / horizontal_range
        relative_n = target_n * tspeed - own_n * ospeed
        relative_e = target_e * tspeed - own_e * ospeed
        closure_m_s = -(relative_n * los_n + relative_e * los_e)

        camera = _gaussian(los_angle, self._config.camera_sigma_deg)
        tail = _gaussian(tail_angle, self._config.tail_sigma_deg)
        alignment = _gaussian(heading_alignment, self._config.heading_sigma_deg)
        altitude = _gaussian(altitude_delta, self._config.altitude_sigma_m)
        closure = _gaussian(closure_m_s, self._config.closure_sigma_m_s)
        range_core = _gaussian(
            slant_range - self._config.lock_range_ideal_m,
            70.0,
        )
        if slant_range < self._config.lock_range_min_m:
            range_core *= _gaussian(
                self._config.lock_range_min_m - slant_range,
                12.0,
            )
        elif slant_range > self._config.lock_range_max_m:
            range_core *= _gaussian(
                slant_range - self._config.lock_range_max_m,
                65.0,
            )

        # Geometric mean prevents one bad dimension from being hidden by four
        # excellent ones while staying continuous (no 0/100 cliff).
        weighted_log = (
            0.29 * math.log(max(camera, 1e-8))
            + 0.25 * math.log(max(tail, 1e-8))
            + 0.17 * math.log(max(alignment, 1e-8))
            + 0.13 * math.log(max(range_core, 1e-8))
            + 0.09 * math.log(max(altitude, 1e-8))
            + 0.07 * math.log(max(closure, 1e-8))
        )
        return _clamp(math.exp(weighted_log), 0.0, 1.0)

    @staticmethod
    def _reason(
        candidate: _PreCandidate,
        lock_probability: float,
        best_start: float | None,
    ) -> str:
        pieces: list[str] = []
        if candidate.tail_access_score >= 75:
            pieces.append("arkaya yerleşme kolay")
        elif candidate.tail_access_score >= 50:
            pieces.append("arkaya yerleşme uygun")
        else:
            pieces.append("arkaya yerleşme zor")
        pieces.append(f"birleşme ≈ {candidate.merge_eta_s:.1f} sn")
        if candidate.turn_required_deg >= 100.0:
            pieces.append(f"ilk dönüş ağır ({candidate.turn_required_deg:.0f}°)")
        elif candidate.turn_required_deg >= 55.0:
            pieces.append(f"ilk dönüş belirgin ({candidate.turn_required_deg:.0f}°)")
        if best_start is not None:
            pieces.append(f"4 sn kilit başlangıcı ≈ {best_start:.1f}. sn")
        else:
            pieces.append("kesintisiz 4 sn pencere bulunamadı")
        if candidate.state.age_s <= 0.6:
            pieces.append("telemetri güncel")
        elif candidate.state.age_s <= 1.3:
            pieces.append("telemetri kabul edilebilir")
        else:
            pieces.append("telemetri yaşlı")
        pieces.append(f"kilit şansı %{lock_probability * 100.0:.0f}")
        return " • ".join(pieces)
