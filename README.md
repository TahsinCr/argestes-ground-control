[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Ground Control Station

A desktop ground-control application with an extension host. The built-in
MAVLink protocol extension and optional Vehicle Connection extension provide the current
vehicle connection, telemetry/GPS and autopilot behavior. Feature packages provide
map/autonomy, parameters, battery, flight dynamics, configurable telemetry, quick
commands, cameras, joystick and competition servers. The common application owns
the workspace, settings, logs, extension lifecycle and normalized data contracts.

The application is maintained as a vehicle-neutral ground-control foundation.

## Quick start

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
git submodule update --init --recursive
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r extensions/mavlink/requirements.txt
pip install -r extensions/map_autonomy/requirements.txt
python main.py
```

The application requires Python 3.11+ and Qt 6. Protocol and WebEngine dependencies
are installed from their owning extension's requirements; use the same virtual
environment to launch the application. Keep Qt and Qt WebEngine versions aligned.
On Linux, install the system packages required by PyQt6 and Qt WebEngine before
launching the application.

## MAVLink connections

Create and select named MAVLink profiles from the telemetry connection control.
Common endpoints include:

```text
Serial: /dev/ttyUSB0     baud: 57600
UDP:    udpin:0.0.0.0:14550
TCP:    tcp:127.0.0.1:5762
```

An open endpoint is not considered healthy until the vehicle heartbeat is
received. `auto_connect` controls the initial attempt; `autoreconnect` controls
recovery after a live link is lost. With both disabled, no background attempt
is made. An explicit disconnect suppresses recovery until the operator
reconnects.

Only the shared MAVLink client/router reads the transport. Services consume
its events; UI code does not create a second reader or encode raw packets.

## Architecture

```text
Qt UI → UI adapters → application contracts → extension host → extensions/
                            │                                  ├── mavlink/
                            │                                  ├── vehicle_connection/
                            │                                  ├── camera/
                            │                                  ├── map_autonomy/
                            │                                  ├── joystick/
                            │                                  ├── siha_teknofest_server/
                            │                                  ├── battery/, parameters/
                            │                                  └── flight_dynamics/, telemetry_display/, quick_commands/
                            └── workspace, settings, logs and normalized data hubs
```

`src/container.py` is the composition root. `src/extension/api` defines the
versioned extension contract; `src/extension/host` discovers static manifests,
checks capabilities and composes enabled extensions. An extension may use the
public `src.core` packages directly. Protocol adapters provide the common
vehicle contracts and decoded telemetry. The separate Vehicle Connection
extension owns connection lifecycle and profile editing through protocol-neutral
`ConnectionSource` and `ConnectionProfiles` contracts. One protocol provider
must remain enabled. Camera and Vehicle Connection
switches apply immediately without restarting the application; only their owned
services and UI are rebuilt. The Extensions button beside Settings opens a
responsive dialog with search, category/status filters and extension controls.
Its installed-package view can share the tab shell with a future store; no store
is included. Existing MAVLink configuration
remains in `data/settings.json`; extension state and settings share its
`extensions.<identifier>.enabled` and `extensions.<identifier>.config` fields.
Disabling an extension never erases its settings. Legacy files in
`data/extensions/` are imported before removal; unrelated data is left intact.
Wire subscriptions and heartbeat decoding belong to the protocol package;
parameter-message handling belongs to the parameter package's protocol adapter,
not the shared `VehicleService` contract. MAVLink owns
its decoded feed, GPS and autopilot indicators. Vehicle Connection contributes
the connection chip and quality meter;
camera status controls belong to the camera package. Status contributions use
`registrar.status_widget(..., placement="connections" | "metrics", order=100)` to select
the left connection group or the group beside the battery. The connection chip
opens its profile popup; GPS and autopilot indicators remain status-only.
Lower `order` values appear first within their group; camera connections sit
beside vehicle connections without protocol-specific code in the application.
Panel menus offer a persistent size lock. Vehicle telemetry settings select and
order 58 fields, with list/grid layouts and optional category grouping.
TEKNOFEST provides a bottom-bar popup for multiple server profiles, not a
workspace panel. Every extension's settings, including MAVLink's
advanced options, are available only in the common Settings dialog. Existing
bottom-bar camera connection/output popups remain available. The dialect selector lists installed
pymavlink dialects; changing protocol options reconfigures an active connection
on a background worker, without enabling an inactive connection automatically.
Each extension owns `i18n/tr.json` and `i18n/en.json`. Installed catalogs are
validated and merged once, then translations use cached dictionaries. Duplicate
keys are rejected; discovery refresh rebuilds the catalogs without importing
disabled extension code. Legacy `telemetry` enablement is migrated to
`vehicle_connection`, preserving disabled state and existing connection profiles.
The camera extension is disabled by default. Its source and output profiles are
stored in `data/settings.json` under `extensions.camera.config`; legacy camera
profiles in `data/settings.json` are imported without deleting that file.
Camera connections, recording and live output controls are contributed by the
extension and disappear from the common UI when it is disabled. Newly installed
extensions can be discovered with Refresh; they remain off until explicitly
enabled. Reload restarts only an isolated extension after settings changes.
The active vehicle protocol cannot be reloaded in isolation while shared
vehicle services use it.

An extension lives at `extensions/<identifier>/` with a `manifest.json` whose
`api_major` is `1`, `entry_point` is `extensions.<identifier>.plugin:create_extension`,
and `provides`/`requires` list capability identifiers. Its factory returns an
object with `configure(registrar, context)` and optional `start(context)` and
`stop()` hooks. `registrar.singleton(token, factory, dependencies=...)` supplies
services to the application; duplicate service ownership is rejected. See the
built-in extensions for working examples. `registrar.visual_view(...)` contributes
an ordinary workspace panel; `status_widget(...)` contributes a footer indicator.
`registrar.settings_page(factory)` supplies one extension-owned settings widget.
Its factory receives a Qt parent and runs lazily on the UI thread when the user
selects that extension's Settings category. A page may implement
`prepare_shutdown()` to release subscriptions, timers or workers when Settings
closes. Disabled extensions use a typed configuration editor without importing
their UI or starting their services. The former `sidebar_view` and `settings_ui`
contributions have been removed; migrate them to `visual_view` and `settings_page`.
The extension browser manages activation and discovery, not duplicate settings.

## Workspace

The map stays at the center. Flight dynamics, vehicle telemetry, quick commands,
logs, parameters, and extension-provided views are independent panels.
Use View > Panels to show or hide individual panels, and their dividers to resize
them. A compact hover-only grip and menu handles moving, tabbing and floating;
there is no separate Edit Layout mode. Map Focus, Reset Layout and Dark/Light
theme choices remain in View.

The activity bar's upper section selects saved views; its lower, distinct section
toggles panel categories belonging to the active view. The plus button below the
views creates either item with a shared name and searchable standard-icon picker.
A new view starts empty with concise panel instructions. A new category enters
panel-selection mode: click visible panels, then Add or Cancel at the lower right.
Selection intercepts panel clicks, so it cannot trigger vehicle commands. Partially
visible categories open all their available members; fully open categories close
together. Right-click a view or category to rename, change its icon or delete it;
the last view cannot be deleted.

Each view saves its own split/tab layout, panel sizes, visibility and categories in
`data/workspace-layout.json`, with debounced writes and a final save on close.
Switching views does not resize the application window. Existing single-layout
files are migrated to an initial view. Qt's saved split/tab state
is authoritative at startup; independent size hints are not replayed over it.
Empty spacer docks are not used: adjacent split panels
fill their row or column, and unrelated split branches keep their own sizes.

## Optional integrations

* **Map & Autonomy** (`map_autonomy`) owns the map widget, web assets, tile cache,
  planner, preferences and mission view. Its reusable API is
  `extensions.map_autonomy.api.planner.PlannerMapApi`. It uses
  normalized telemetry and navigation services, currently provided by MAVLink.
  It can be disabled or re-enabled without restarting the application.
* **Joystick** restores RC1–RC16 mapping, PWM calibration, input detection and
  button actions. It is disabled by default and currently selects the MAVLink
  RC adapter. Device activation is separate from enabling the extension;
  deactivation releases RC overrides. Protocol selection lives outside sampling
  and UI code, using `src.extension.api.protocol.ActiveProtocol`.
* **SIHA TEKNOFEST Server** (`siha_teknofest_server`) provides independently controlled HTTP server profiles, fixed
  1 Hz telemetry scheduling, server clock tracking, QR/HSS requests and real
  lock/kamikaze result submission methods. It consumes normalized telemetry,
  not MAVLink messages, and does not require the MAVLink package by identity.
  Server exchanges are recorded in `data/teknofest/history.sqlite3` using WAL;
  login credentials are omitted. After the first valid position, the last
  snapshot continues to be sent during vehicle loss, with estimated server time.
  No server is contacted unless its profile has auto-connect enabled or the
  operator explicitly connects. The extension itself is disabled by default.

Configuration layout:

```json
{
  "extensions": {
    "camera": { "enabled": false, "config": {} },
    "joystick": { "enabled": false, "config": {} },
    "siha_teknofest_server": { "enabled": false, "config": { "servers": [] } }
  }
}
```

Existing typed backend fields remain compatibility aliases. The MAVLink package
projects its protocol settings and vehicle connection profiles into the shared
extension section, so legacy editors cannot overwrite another extension's config.

Flight dynamics, telemetry display, quick commands and battery are separate
packages consuming normalized data or generic command capabilities. Telemetry
display settings select visible fields. Parameters currently require the
`mavlink.parameters` capability and keep their MAVLink adapter inside the package.
`VehicleService` does not prescribe RC or mission methods: optional `RcControl`
and `MissionControl` ports in `src.extension.api.vehicle` are advertised separately.
To add another protocol, implement the required port/adapter and capability;
feature UI code must not import its transport. Production mock telemetry has been
removed; tests inject their own doubles. Old `map` and `teknofest` configuration
keys migrate without losing disabled states or unknown settings.

## Settings, logs and tests

Settings are stored in `data/settings.json`; structured logs are stored in
`data/logs/gcs_<session-date>_<session-time>.json`. Each session is a valid JSON
array. All extensions use the same bounded-memory log service; the host adds
`extension_id` to injected loggers and captures extension-origin Python logs.
Run the application test suite with:

```bash
python run_tests.py
```

Qt tests use the offscreen platform. Before field operation, verify the target
MAVLink connection and mission-transfer behavior with the intended vehicle.

## License

Copyright © 2026 TahsinCr.
