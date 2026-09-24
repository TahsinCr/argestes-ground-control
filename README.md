[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Ground Control Station

A desktop ground-control application for MAVLink-compatible vehicles. The
current common application provides telemetry and GPS, ArduPilot/MAVLink
connection management, mission planning, parameter management, maps, logs,
battery status and device/connection health.

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
Qt UI → UI adapters → application services → vehicle adapter → Core
                                  ├── MAVLink, telemetry and parameters
                                  ├── planner and vehicle commands
                                  └── settings, logs, map cache and battery
```

`src/container.py` is the composition root. Dependencies are passed through
constructors. `src/core` contains shared event, dependency and MAVLink
infrastructure; `src/models` contains UI-independent models; `src/services`
contains application and vehicle behavior; `src/ui` contains Qt presentation.

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
