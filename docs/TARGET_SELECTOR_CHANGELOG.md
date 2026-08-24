# Target Selector Integration Changelog

## 2026-08-24 — Backend integration into current GCS

- Ported the GCS-only target ranking/simulation engine from `GCS-Target-Selector` onto the current Point-Selector + Kamikaze Transit GCS baseline.
- Did **not** port any `src/ui/**` changes from the Target-Selector branch.
- Kept the existing elimination → pre-score → top-10 simulation → top-5 ranking algorithm.
- Top-5 refresh cadence is fixed by `TargetSelectionConfig.refresh_interval_s` and defaults to **2 seconds**.
- Added explicit operator lock through `TargetSelectionService.select_target(team_number)`.
- Once a target is selected, Top-5 ranking is frozen until `clear_target()` is called.
- The selected team number never auto-switches when rankings change or telemetry is temporarily lost.
- Every new TEKNOFEST telemetry frame for the selected team is forwarded through the existing typed `apn.target.telemetry` communication contract.
- Fixed the live transport to use the contract's ACKed request path (`OrinService.issue`) instead of notification send; this matches the onboard `ApplicationMessageService` request handler and prevents the real MAVLink contract gateway from rejecting the stream.
- Selected target `CompetitorTelemetry.raw` is preserved in GCS so future APN adapters can consume additional TEKNOFEST fields without changing selector math.
- Stale/missing selected telemetry is not replayed as fresh data; the target remains locked and a warning is logged until the same team recovers.
- No siha-onboard/APN receiver code is changed in this step.
