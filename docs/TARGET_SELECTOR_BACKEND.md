# Target Selector Backend Integration

This integration is intentionally UI-independent. `src/ui/**` is unchanged.

## Runtime service

`RuntimeServices.target_selection_service` exposes the backend.

### Live Top-5

- `service.updated`: publishes a new `TargetSelectionResult` every 2 seconds while unlocked.
- `service.last_result`: most recent/frozen Top-5.
- `service.team_history(team_number)`: recent TEKNOFEST telemetry trail for one team.
- `service.histories_snapshot()`: recent trails for all teams.

### Operator target lock

Call:

```python
service.select_target(team_number)
```

The team must be in the current Top-5. This immediately locks the Top-5 and the team number. Ranking stays frozen even if `rank_targets()` is called manually.

The selected team's live `CompetitorTelemetry` continues to update. `CompetitorTelemetry.raw` preserves the complete TEKNOFEST row.

Call `service.clear_target()` to unlock and resume the 2-second Top-5 refresh.

### Events for a future UI adapter

- `service.selection_changed`: selected team number or `None`.
- `service.selected_telemetry_updated`: fresh selected-team telemetry; publishes `None` on stale/lost transition.

## GCS → APN transport

Each fresh TEKNOFEST frame for the locked team is normalized into the existing shared contract:

```text
apn.target.telemetry
```

Fields:

```text
takim_numarasi
iha_enlem
iha_boylam
iha_irtifa
iha_dikilme
iha_yonelme
iha_yatis
iha_hizi
zaman_farki
```

No custom `combat.target.*` protocol was added. The onboard/APN receiver will be connected in the separate siha-onboard integration step.

If Orin is disconnected, selection remains locked and the service waits for later fresh TEKNOFEST frames; it does not auto-select another team. Stale/lost telemetry is never replayed as fresh coordinates.
