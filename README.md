[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Ground Control Station

A desktop ground-control application with an extension host. The built-in
MAVLink protocol extension and optional telemetry extension provide the current
vehicle connection, telemetry/GPS, autopilot and parameter behavior. An optional
camera extension adds the multi-camera wall, recording and live video output.
The common application retains mission planning, maps, logs and battery/device status.

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
python main.py
```

The application requires Python 3.11+, Qt 6, `pymavlink` and `pyserial`.
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
                            │                                  ├── telemetry/
                            │                                  └── camera/
                            └── settings, logs, planner, map cache and battery
```

`src/container.py` is the composition root. `src/extension/api` defines the
versioned extension contract; `src/extension/host` discovers static manifests,
checks capabilities and composes enabled extensions. An extension may use the
public `src.core` packages directly. Protocol adapters provide the common
vehicle contracts and a telemetry-source factory; the separate telemetry
extension consumes that factory, so it can be disabled without removing the
protocol. One protocol provider must remain enabled. Camera and telemetry
switches apply immediately without restarting the application; only their owned
services and UI are rebuilt. The left activity bar provides search,
category/status filters and extension controls. Existing MAVLink configuration
remains in `data/settings.json`; extension state and extension-owned settings
are stored under `data/extensions/` and are not erased when disabled.
The camera extension is disabled by default. Its source and output profiles are
stored in `data/extensions/camera/settings.json`; on first use, legacy camera
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
built-in extensions for working examples. An extension can also call
`registrar.sidebar_view(identifier, title, factory)` during configuration;
the factory receives the Qt parent and creates a left-sidebar widget on the UI
thread. This keeps presentation contributions out of Core and avoids importing
disabled extension UI modules.
Extensions can also contribute a visual workspace widget, footer status widget,
or settings action through `visual_view`, `status_widget`, and `settings_ui`.
The camera extension uses these to keep capture and output controls out of the
common window.

## Workspace

The map stays at the center. Flight dynamics, vehicle telemetry, quick commands,
logs, parameters, and extension-provided views are independent panels.
Normal mode keeps panel headers out of the way: use the left activity rail or
View > Panels to show or hide panels at any time, and resize their dividers
directly. View > Edit Layout temporarily reveals handles for moving, tabbing
and floating panels; turn it off when finished. The View menu also provides
Map Focus, Reset Layout, and a Dark/Light theme switch.
The panel icon in the left rail opens a quick switcher with live panel
visibility, grouped by vehicle, bottom, and extension panels. Hiding or showing
panels there does not replace the saved arrangement.
Panel positions and visibility are saved in `data/workspace-layout.json` when
the application closes; extension settings remain separate.

## Settings, logs and tests

Settings are stored in `data/settings.json`; structured logs are stored in
`data/logs/yki.jsonl`. Run the application test suite with:

```bash
python run_tests.py
```

Qt tests use the offscreen platform. Before field operation, verify the target
MAVLink connection and mission-transfer behavior with the intended vehicle.

## License

Copyright © 2026 TahsinCr.
