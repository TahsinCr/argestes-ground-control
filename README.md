[![Python 3.11+][python-shield]][python-url]
[![License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Ground Control Station

Kırlangıç Ground Control Station is a desktop application for operating
MAVLink-compatible vehicles. It combines vehicle connectivity, live telemetry,
mission planning, parameters, operator commands, maps and camera streams in
one interface.

The `main` branch is intentionally vehicle- and product-neutral. It provides
the shared ground-control foundation; product-specific automation belongs in a
separate integration layer rather than in this runtime.

## Features

- One MAVLink transport reader for serial, UDP and TCP connections
- Live telemetry, GPS, battery, packet-health and radio-link presentation
- Capability-aware ARM/DISARM, mode, GOTO, TAKEOFF, LAND, RTL, HOLD and
  mission controls
- Selectable Linux joysticks with RC1–RC16 PWM calibration, deadzone, expo,
  reversal, button modes and safe override release
- Mission planning, undo/redo, vehicle mission synchronization and parameters
- Named camera profiles and 1, 2, 3 or 4-pane video-wall layouts
- RTSP, HTTP/MJPEG, RTP/UDP, V4L2, file and custom GStreamer video sources
- Multiple video-share targets with source, transport, codec, FPS, timestamp
  and network-interface configuration
- Independent per-camera MP4 recording at a fixed 30 FPS through supported
  hardware encoders, with same-file pause/resume across temporary feed loss
- Persistent per-camera aiming reticles that remain scoped to visible frames
- Structured logs, map cache, archives and Turkish/English UI translation
- Pixhawk-reported or voltage-estimated battery percentage

## Quick start

Clone the project and its pinned shared submodules:

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
git submodule sync --recursive
git submodule update --init --recursive
```

On Linux, create an environment that can access system GStreamer bindings:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

The application requires Python 3.11+, Qt 6 and the dependencies in
`requirements.txt`. Camera sources additionally require GStreamer plugins
appropriate for their transport and codec.

## Video dependencies

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

Verify a camera installation with:

```bash
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
```

## Connection behaviour

Configure MAVLink profiles in Settings or from the telemetry menu. Typical
endpoints are:

```text
Serial: /dev/ttyUSB0     baud: 57600
UDP:    udpin:0.0.0.0:14550
TCP:    tcp:127.0.0.1:5762
```

An open endpoint is not considered healthy until the target heartbeat is
received. The status UI uses yellow while connecting, red when unavailable or
failed and green after health is verified.

The connection controller owns automatic behaviour:

- `auto_connect` allows one initial connection attempt at application start.
- `autoreconnect` allows bounded retry after a previously live connection is
  lost.
- If both are disabled, no background connection attempt is made.
- An explicit operator disconnect cancels any pending recovery and keeps the
  transport closed until the operator connects again.

Only the shared `MavlinkClient` and router may read a MAVLink transport. New
services must consume its events and must not create a second reader, serial
connection or ACK consumer.

## Cameras and video sharing

Camera connections are saved as named profiles. A source can appear in only
one video-wall pane; selecting a source already displayed in another pane swaps
the two panes. The video wall supports one, two, three and four views.

Video sharing always publishes camera frames, not a desktop capture. Each
target selects a source, transport, codec, FPS, fixed-frame-rate setting,
timestamp setting and network interface. Frames are sent through a bounded
pipeline so a slow receiver does not degrade the capture or UI path.

The same output editor can create local recording targets. Recordings are
camera-specific MP4 files at a constant 30 FPS and require a supported NVIDIA
or VA-API hardware encoder. A temporary camera interruption pauses that
camera's timeline; when frames return, writing resumes in the same file. The
camera-panel record control starts or stops all configured camera recordings,
while each target can also be enabled independently in Video Share.

Each camera profile can retain its own aiming point. The reticle is drawn only
inside the rendered frame and survives camera switching and application
restarts.

## Architecture

```text
Qt UI → UI adapters → application services → vehicle adapters → core
                                  │
                                  ├── MAVLink connection / telemetry / parameters
                                  ├── planner and capability-aware commands
                                  ├── camera capture and video sharing
                                  └── settings / logs / cache / archives
```

`src/container.py` is the composition root. It wires infrastructure, vehicle
adapters, application services and runtime lifecycle in a single explicit
place. Services receive constructor dependencies and do not resolve a global
container themselves.

- `src/core`: events, dependency primitives and MAVLink infrastructure
- `src/communication`: shared, transport-neutral contract models
- `src/models`: validated data models independent from the UI
- `src/services/vehicle`: connection control, MAVLink, telemetry and parameters
- `src/services/joystick`: physical controller discovery and RC PWM translation
- `src/services`: commands, planner, settings, video, logs, cache and archives
- `src/ui`: Qt views, widgets and presentation adapters

UI code does not construct raw MAVLink packets or run blocking work. Vehicle
actions are defined in service-level command definitions and reported to the
operator only after acknowledgement or verified state change.

## Settings, logs and tests

Persistent settings are stored in `data/settings.json`. When adding a setting,
update the validated model, settings UI and both language files together.
Structured events are stored in `data/logs/yki.jsonl`; plans and session data
are stored under `data/`.

Run the complete test suite with:

```bash
python run_tests.py
```

Qt tests use the offscreen platform. Before field use, also perform a hardware
smoke test with the intended MAVLink endpoint, camera source and mission
transfer path.

## License

Copyright © 2026 TahsinCr.

Licensed under GNU General Public License version 3 (`GPL-3.0-only`). See
[LICENSE](LICENSE) and [COPYRIGHT](COPYRIGHT).

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white
[python-url]: https://www.python.org/
[license-shield]: https://img.shields.io/badge/License-GPL--3.0--only-3DA639?style=for-the-badge
[license-url]: LICENSE
[lang-tr-url]: README-TR.md
