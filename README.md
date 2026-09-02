[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Team Ground Control Station

Kırlangıç Team Ground Control Station (YKİ) is a common ground-control
application for MAVLink-compatible vehicles. It brings flight-controller
connectivity, telemetry, mission planning, vehicle commands, parameters, maps
and camera streams into one desktop interface.

The `main` branch is the common GCS layer and is not coupled to a specific
product or mission algorithm. SIHA-specific behavior such as kamikaze,
target selection, competition-server integration and onboard task automation
does not execute in this runtime; it belongs to a separate integration layer.
This keeps the application usable with fixed-wing, multirotor and other
MAVLink vehicles.

## Features

- Single-reader MAVLink connectivity over serial, UDP and TCP
- Live telemetry, GPS, battery, packet-health and RFD/radio-link information
- ARM/DISARM, mode changes, GOTO, TAKEOFF, LAND, RTL, HOLD and mission
  upload/start/pause/resume controls
- Command and flight-mode UI adapted to the capabilities learned from the
  connected vehicle
- Takeoff, waypoint, loiter, RTL, land and safe-area planning with undo/redo
  and vehicle mission synchronization
- Parameter catalogue, default-parameter source and verified parameter writes
- RTSP, HTTP/MJPEG, RTP/UDP, V4L2, file and custom GStreamer video sources
- Multiple camera profiles, 1/2/3/4-pane video wall and immediate source swaps
- Multiple video-share targets that publish a camera or focused-panel source,
  with fixed-FPS and timestamp settings
- Map cache, structured JSONL logs and Turkish/English UI translation
- Voltage-estimated or Pixhawk-reported battery percentage with independent
  warning and failsafe thresholds

## Quick start

Clone the repository together with its two shared submodules:

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
```

For an existing checkout:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

On Linux, create a virtual environment that can also see the system GStreamer
bindings:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

YKİ requires Python 3.11 or newer, Qt 6 and GStreamer plugins suitable for the
selected camera type. It is designed for a desktop ground-control computer;
algorithm and stream services should remain separate on resource-constrained
onboard computers.

## Platform and video dependencies

| Platform | Recommendation |
|---|---|
| Arch Linux | Current Python, Qt 6 and GStreamer packages |
| Ubuntu | 24.04 LTS or an equivalent Python 3.11+ environment |
| Windows | Python 3.11+ and same-architecture MSVC GStreamer |
| macOS | Native-architecture Python, Qt and GStreamer |

Arch Linux:

```bash
sudo pacman -S --needed \
  gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav python-gobject
```

Ubuntu:

```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-dev python3-gi \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
```

Verify the installation with:

```bash
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
```

USB sources use `v4l2src` on Linux. On Windows, use a network source or an
appropriate custom GStreamer pipeline.

## Connection and runtime principles

Configure the MAVLink endpoint from Settings or the telemetry menu in the
bottom status bar. Typical endpoints are:

```text
Serial: /dev/ttyUSB0     baud: 57600
UDP:    udpin:0.0.0.0:14550
TCP:    tcp:127.0.0.1:5762
```

Opening a transport does not prove a healthy vehicle connection. Vehicle state
requires the target heartbeat, video requires fresh frames, and GPS requires a
recent valid fix. The interface uses yellow while attempting a connection, red
for an unavailable or failed connection and green only for a verified live
state.

Only `MavlinkClient` and its router read the MAVLink transport in one
application session. New services must not add a second `recv_match()` loop,
serial reader or independent ACK consumer.

## Cameras and video sharing

Camera connections are saved as profiles. A profile contains its source type,
address, codec, latency, timeout and auto-connect setting. A source can appear
in only one split-screen pane at a time. Left- or right-click a camera name to
select another source; if it is already shown in another pane, the two panes
swap sources.

The video panel supports 1, 2, 3 and 4 views. Panel tools remain fixed while
the camera wall changes: recording is at the lower left, layout selection is
at the lower centre, and display mode/reconnect controls are at the lower
right.

Video sharing publishes camera frames only; desktop or screen capture is not a
valid source. Each share target can select a source, transport, codec, FPS,
fixed-frame-rate option, timestamp option and network interface. Fixed frame
rate and timestamps are enabled by default for specification compliance, and
FPS must be at least 15.

## Architecture

```text
Qt UI → UI adapters → services → models → core
                         │
                         ├── vehicle: MAVLink, telemetry, parameters
                         ├── planner: plan state and vehicle mission sync
                         ├── video: GStreamer sources and shares
                         ├── settings / logs / tile cache / archive
                         └── commands: capability-aware dispatch
```

`src/container.py` is the single composition root. It registers
infrastructure, vehicle adapters, application services and runtime lifecycle
in order. Services receive dependencies through constructors and never resolve
a global container themselves.

- `src/core`: shared events, dependency management and MAVLink infrastructure
- `src/communication`: transport-neutral communication contracts; it does not
  own a connection in the `main` runtime
- `src/models`: validated data models independent from the UI
- `src/services/vehicle`: MAVLink, telemetry and parameter adapters
- `src/services`: planner, commands, video, settings, archive, logs and cache
- `src/ui`: Qt, Leaflet and presentation/interaction code only

The command catalogue is transport- and vehicle-type-neutral. The connected
adapter's `VehicleCapabilities` determines which commands and flight modes are
available. For navigation requests such as `GOTO`, the newest request
supersedes an older pending request so that a stale destination cannot be sent
late.

Legacy SIHA connection, competition, target-selection and onboard-task
surfaces are inactive in the common backend. Passive compatibility adapters
keep the UI constructible during the transition, but they do not send network
requests or vehicle commands.

## Map, telemetry and battery

Telemetry ingestion is separate from UI rendering. The backend processes every
MAVLink packet while the Qt layer draws only the most recent presentation
state. Map and parameter updates are coalesced so high-rate input cannot build
up a UI queue.

The planner separates operator-owned routes from externally supplied layers.
Safe areas, forbidden areas and vehicle-downloaded missions cannot erase each
other's owned data. Home updates and mission transfers are not reported as
successful without vehicle confirmation.

The battery display can use the Pixhawk percentage or estimate one from cell
count, full voltage and reserve voltage. Display, warning and failsafe
thresholds are independent; Pixhawk remains the final authority for flight
safety.

## Settings, logs and archives

Persistent settings are stored in `data/settings.json`. Models validate input
and migrate legacy single-camera/single-MAVLink settings to compatible profile
collections. When adding a setting, update its model validation, settings UI
schema and both language files together.

Structured events are written to `data/logs/yki.jsonl`. The log panel supports
text search and level filtering without interrupting an operator reviewing
older entries. Mission plans and session data are stored under `data/`.

## Development rules

1. Define a vehicle action through `CommandDefinition` and `VehicleService`
   before adding a UI control; never construct raw MAVLink packets in Qt.
2. Keep MAVLink constants, target IDs and ACK filters in `services/vehicle`.
3. The UI consumes models and service events only; business rules, network I/O
   and blocking work do not run on the UI thread.
4. Use `VideoSourceConfig` and `VideoShareConfig` validation for new camera
   sources and publishing targets.
5. A real vehicle action creates a structured log. Report success to the
   operator only after an ACK or verified vehicle-state change.

## Testing

```bash
python run_tests.py
```

The runner executes YKİ and both shared-submodule suites as one result. Qt
tests use the offscreen platform; real hardware still requires a MAVLink
endpoint, camera-source and mission-transfer smoke test.

## License

Copyright © 2026 TahsinCr.

This project is licensed under GNU General Public License version 3
(`GPL-3.0-only`). See [LICENSE](LICENSE) and [COPYRIGHT](COPYRIGHT) for the
full terms.

<!-- Badge images -->

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white
[python-url]: https://www.python.org/
[license-shield]: https://img.shields.io/badge/License-GPL--3.0--only-3DA639?style=for-the-badge
[license-url]: LICENSE
[lang-tr-url]: README-TR.md
