[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe](README-TR.md)

# Kırlangıç Ground Control Station

Kırlangıç is a desktop ground control station built around an extensible application host. The current vehicle stack uses MAVLink, while the application and feature packages are organized so that vehicle protocols and operator tools can evolve independently.

The application owns shared workspace behavior, settings, logging, extension lifecycle and normalized data contracts. Extensions provide vehicle connections, maps, camera tools and other operator features.

## Quick start

```bash
git clone --recurse-submodules \
  https://github.com/TahsinCr/ground-control-station.git
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

Python 3.11+ and Qt 6 are required. Install protocol and WebEngine dependencies from the requirements files belonging to those extensions, and run the application from the same virtual environment. Keep Qt and Qt WebEngine versions aligned. Linux may also require system packages for PyQt6 and Qt WebEngine.

## Connecting a vehicle

Create or select a named profile from the vehicle connection control. Typical MAVLink endpoints are:

| Link | Endpoint | Example baud |
| --- | --- | --- |
| Serial | `/dev/ttyUSB0` | `57600` |
| UDP input | `udpin:0.0.0.0:14550` | — |
| TCP | `tcp:127.0.0.1:5762` | — |

The connection is healthy after a vehicle heartbeat is received. `auto_connect` controls the initial connection attempt; `autoreconnect` controls recovery after an established link drops. If both are disabled, the application makes no automatic attempt. An operator disconnect suppresses recovery until the next explicit connection request.

The shared MAVLink client and router are the only transport readers. Services consume their events; UI code does not create another reader or encode packets.

## Architecture

### Application and extension host

`src/container.py` composes the application. The versioned extension contract lives in `src/extension/api`; `src/extension/host` discovers manifests, checks capabilities and starts enabled packages. Extensions can use public `src.core` packages directly.

The application provides shared workspace, settings, logging and normalized data services. A protocol extension adapts its vehicle data to common contracts. The Vehicle Connection extension manages connection lifecycle and profiles through provider-neutral `ConnectionSource` and `ConnectionProfiles` contracts. At least one vehicle protocol provider must remain enabled.

### Extension packages

Each package lives under `extensions/<identifier>/` and has a `manifest.json` with its API version, entry point and provided or required capabilities. Its factory returns an object with `configure(registrar, context)` and optional `start(context)` and `stop()` methods. `registrar.singleton(...)` supplies application services; duplicate service ownership is rejected.

Extensions contribute ordinary panels with `registrar.visual_view(...)`, footer indicators with `registrar.status_widget(...)`, and settings pages with `registrar.settings_page(factory)`. Settings pages load only when selected and may implement `prepare_shutdown()` to release timers, workers and subscriptions. Disabled extensions do not start services or import their UI. The extension browser manages discovery and activation; settings remain in the shared Settings window.

The active protocol cannot be reloaded independently while shared vehicle services depend on it. Camera and Vehicle Connection can be enabled or disabled at runtime, rebuilding only their own services and UI.

### Configuration and language

Application settings and extension state live in `data/settings.json`. Each extension stores its state and configuration under `extensions.<identifier>.enabled` and `extensions.<identifier>.config`; disabling a package does not erase its settings. Legacy files under `data/extensions/` are imported before removal, without deleting unrelated data.

Every extension owns `i18n/en.json` and `i18n/tr.json`. The host validates and merges installed catalogs once, caches them, and rejects duplicate keys.

## Workspace

The workspace contains independent panels for the map, flight dynamics, vehicle telemetry, quick commands, logs, parameters and extension-provided views. Use **View → Panels** to show or hide panels. Drag the small handle above a panel to move, split or tab it; the adjacent menu offers placement and visibility actions. There is no separate layout-edit mode.

The activity bar has saved views above and panel categories below. A view stores its own panel visibility, split and tab arrangement, sizes and categories in `data/workspace-layout.json`. Switching views does not resize the application. Existing single-layout files migrate to an initial view. Adjacent panels share the dimensions of their split, while separate branches can be resized independently.

Panel menus can lock a panel's content size. This preference is saved with its view. A locked panel can limit how small the application window can become.

## Built-in and optional packages

The MAVLink package provides the current protocol, decoded vehicle feed, GPS and autopilot indicators. Parameters use the `mavlink.parameters` capability and keep their protocol adapter in the parameter package. The common `VehicleService` does not require RC or mission methods; optional `RcControl` and `MissionControl` contracts are advertised separately.

Other packages include Map & Autonomy (`map_autonomy`), Camera, Joystick, Battery, Flight Dynamics, Telemetry Display, Quick Commands and SIHA Teknofest Server. Their settings are managed from their categories in the shared Settings window. Camera and competition server controls appear as bottom-bar popups when those packages are enabled.

Map & Autonomy owns the map, web assets, tile cache and mission planner. Joystick currently uses the MAVLink RC adapter; disabling joystick control releases RC overrides. Telemetry Display lets operators choose and order 58 fields and arrange them as a list or grouped grid. SIHA Teknofest Server supports separate HTTP profiles and records exchanges in `data/teknofest/history.sqlite3`; it sends no requests unless a profile is configured for automatic connection or the operator connects explicitly.

To support another vehicle protocol, implement its required adapter and capabilities. Feature UI should consume normalized data and must not depend on transport details. Production mock telemetry has been removed; tests provide their own doubles.

## Settings, logs and tests

Settings are stored in `data/settings.json`. Each session writes a JSON log to `data/logs/gcs_<session-date>_<session-time>.json`; extension entries retain their `extension_id` in the shared log.

Run the test suite with:

```bash
python run_tests.py
```

Qt tests use the offscreen platform. Before field operation, verify the MAVLink link and mission-transfer behavior with the intended vehicle.

## License

Copyright © 2026 TahsinCr.

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-blue
[python-url]: https://www.python.org/downloads/
[license-shield]: https://img.shields.io/badge/License-GPL--3.0-blue
[license-url]: LICENSE
