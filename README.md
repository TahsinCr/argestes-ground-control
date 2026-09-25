[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Ground Control Station

A desktop ground-control application with an extension host. The built-in
MAVLink protocol extension and optional telemetry extension provide the current
vehicle connection, telemetry/GPS, autopilot and parameter behavior. The common
application retains mission planning, maps, logs and battery/device status.

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
                            │                                  └── telemetry/
                            └── settings, logs, planner, map cache and battery
```

`src/container.py` is the composition root. `src/extension/api` defines the
versioned extension contract; `src/extension/host` discovers static manifests,
checks capabilities and composes enabled extensions. An extension may use the
public `src.core` packages directly. Protocol adapters provide the common
vehicle contracts and a telemetry-source factory; the separate telemetry
extension consumes that factory, so it can be disabled without removing the
protocol. One protocol provider must remain enabled. Enabling/disabling an
extension takes effect after restart. The left activity bar provides search,
category/status filters and extension controls. Existing MAVLink configuration
remains in `data/settings.json`; extension state and extension-owned settings
are stored under `data/extensions/` and are not erased when disabled.

An extension lives at `extensions/<identifier>/` with a `manifest.json` whose
`api_major` is `1`, `entry_point` is `extensions.<identifier>.plugin:create_extension`,
and `provides`/`requires` list capability identifiers. Its factory returns an
object with `configure(registrar, context)` and optional `start(context)` and
`stop()` hooks. `registrar.singleton(token, factory, dependencies=...)` supplies
services to the application; duplicate service ownership is rejected. See the
built-in extensions for working examples.

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
