# Kamikaze Approach — GCS P2 → P1 → QR corridor selector

This module is backend-only. It computes and locks approach corridors for the GCS; it does not modify UI widgets and it does not directly command the aircraft.

## Geometry

All geometry is derived from `KamikazeApproachConfig` in `src/models/kamikaze_approach.py`.

Current defaults:

- `start_altitude_m = 130.0`
- `pullup_altitude_m = 90.0`
- `target_altitude_m = 0.0`
- `dive_angle_deg = 45.0`
- `alignment_nominal_m = 150.0`
- `corridor_width_m = 1.0`
- `hss_margin_m = 5.0`
- `angle_step_deg = 1`
- `max_candidates = 5`

P1 is not a hard-coded distance:

`P1_radius = (start_altitude - target_altitude) / tan(dive_angle)`

The pull-up projection is:

`pullup_radius = (pullup_altitude - target_altitude) / tan(dive_angle)`

P2 is always on the same radial, exactly `alignment_nominal_m` behind P1. It is never moved to work around an HSS zone. If P2 or any part of the P2 → P1 → QR corridor is unsafe, that angular candidate is rejected.

Changing altitude, dive angle, P2-P1 distance, corridor width or HSS margin re-derives the complete geometry on the next unlocked plan build.

## 360° candidate generation

The planner evaluates the full QR perimeter in `angle_step_deg` increments. With the current `1°` setting this produces 360 candidates.

For each angle it creates:

1. P1 at the derived QR-to-P1 radius.
2. P2 another `alignment_nominal_m` behind P1.
3. The P2 → P1 alignment segment.
4. The P1 → QR dive segment.
5. The complete corridor polygon for backend/map consumers.

## HSS hard filter

HSS is never scored. An HSS conflict invalidates the candidate.

The following must all be clear:

- P2 point,
- P1 point,
- P2 → P1 segment,
- P1 → QR segment.

The effective segment exclusion radius is:

`HSS radius + hss_margin_m + corridor_width_m / 2`

With the current defaults, this adds 5.5 m beyond the published HSS radius for the corridor centreline check.

## Wind ranking

Wind is read from ArduPilot `WIND` telemetry. `direction` is treated as wind-from direction. Among HSS-safe candidates, the preferred corridor is the one whose inbound P2 → P1 → QR course most directly faces the incoming wind.

- headwind is best,
- crosswind is worse,
- tailwind is worst.

Wind never overrides HSS safety.

If no valid `WIND` message has been received, the service logs a warning and falls back to HSS-only ordering. If wind speed is below `wind_min_speed_mps` (currently 1.0 m/s), wind ranking is disabled because the direction is not operationally important.

## Top-5 diversification

Candidates are generated every 1°, but the Top-5 should represent distinct alternatives rather than adjacent angles.

The selector first tries an 8° minimum separation. If five candidates cannot be produced, it relaxes through:

`8° → 6° → 4° → 3° → 2° → 1°`

Within nearby alternatives, wind alignment is the primary ordering criterion. Therefore, if 180°, 181° and 182° are all valid and 180° faces the wind most directly, 180° represents that local alternative.

## Selection and wind refresh state

The backend states are:

`idle → selecting → corridor_selected → mission_active`

While `selecting`, the latest ArduPilot wind may trigger a Top-5 recomputation at most every `wind_refresh_interval_s` (currently 120 s).

Once the operator selects a corridor:

- the plan is locked,
- P1/P2 do not move,
- new wind telemetry does not recompute the corridor set.

The same lock remains active during the mission. A new selection session or completed/aborted mission explicitly resets this state.

## Onboard mission handoff

After operator selection, `navigation_handoff()` still exposes the locked geometry for backend/map consumers. Actual flight execution is started only through `KamikazeApproachService.start_selected_mission()`.

The GCS backend then performs this immutable handoff sequence:

1. Refresh and synchronize the current QR target. A locked corridor is rejected if the QR moved by more than 1 m; it is never silently rebuilt.
2. Synchronize the current HSS snapshot, including an explicit empty-zone update.
3. Publish one `kamikaze.approach.update` containing the locked QR, P2, P1, inbound course, dive profile, HSS revision, safety margin and corridor width.
4. Require the onboard ACK for that exact plan/hash.
5. Start the onboard task `kamikaze.transit` with the accepted plan id/revision.
6. Mark the GCS plan `mission_active` only after the onboard start request is accepted.

The GCS does **not** steer P2 -> P1 in real time. Onboard owns execution: unchanged HSS logic routes the aircraft to P2, then GUIDED/L1 is given an internal P_SAFE continuation target beyond P1. P1 is monitored as the pre-dive gate; the existing ArduPilot mission list is never cleared/replaced and AUTO is not forced. If the gate fails, onboard calls the unchanged HSS controller again to return safely to the same locked P2. If the gate passes, onboard locally hands control to the existing kamikaze dive controller without waiting for a second GCS `DAL` command.

UI widgets/layout are intentionally outside this integration.
