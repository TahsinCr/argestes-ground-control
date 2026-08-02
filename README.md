[![Python 3.11+][python-shield]][python-url]
[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![GPL-3.0 License][license-shield]][license-url]

**English** | [Türkçe][lang-tr-url]

# Kırlangıç Team Ground Control Station

Kırlangıç Team GCS brings flight-controller connectivity, telemetry, flight
planning, operator commands, video and TEKNOFEST communication into one
operator interface.

The GCS does not execute autonomous missions. It prepares a flight plan,
dispatches commands to the aircraft or onboard computer, and confirms results
from protocol-level responses. Target locking, kamikaze guidance, computer
vision and similar decision algorithms belong to the onboard system.

## Capabilities

- MAVLink over serial, UDP and TCP for ArduPilot-based aircraft
- Flight telemetry, connection health, GPS, battery and radio-link monitoring
- ARM/DISARM, flight modes, LAND, GOTO, parameters and flight-plan transfer
- Takeoff, waypoint, loiter, RTL, land and local safety-area planning
- Parameter and flight-plan synchronization from the flight controller
- RTSP, HTTP/MJPEG, RTP/UDP, V4L2, file and custom GStreamer sources
- Optional persistent video auto-connect with fresh-frame health validation
- TEKNOFEST server communication, competitor telemetry, HSS and QR references
- Searchable, level-filtered and runtime-translated structured logs
- Independent battery warning and optional failsafe thresholds
- Undo/redo history for map planning and acknowledged home changes
- Resizable map, video, log and parameter workspaces
- Persistent connection and backend settings

## Supported environments

Arch Linux is the team's primary validated environment.
Ubuntu and Windows are also supported. Always verify the actual serial device
and video source on the machine used with flight hardware.

| Environment | Recommended version | Notes |
|---|---:|---|
| Arch Linux | Current | Primary development environment |
| Ubuntu | 24.04 LTS | Package names below target this release |
| Windows | Windows 11 x64 | Use the MSVC x86_64 GStreamer build |
| Python | 3.11+ | Run the application in a virtual environment |

## GStreamer installation

### Arch Linux

```bash
sudo pacman -S --needed \
  gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav python-gobject
```

### Ubuntu 24.04

```bash
sudo apt update
sudo apt install -y \
  python3-venv python3-dev python3-gi \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
```

### Windows 11

Install the 64-bit **MSVC** package from the
[official GStreamer download page](https://gstreamer.freedesktop.org/download/#windows).
GStreamer 1.28 and later include runtime and development files when the
**Development** installation type is selected. Older releases require both
installers.

Add the GStreamer `bin` directory to the user or system `PATH`. The default
system installation is usually:

```text
C:\Program Files\gstreamer\1.0\msvc_x86_64\bin
```

Open a new PowerShell window and verify the installation:

```powershell
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
```

The built-in `usb` source uses Linux `v4l2src`. On Windows, use a network
stream or a custom pipeline unless `ksvideosrc` support has been added.

On Linux, verify the required plugins with:

```bash
gst-inspect-1.0 --version
gst-inspect-1.0 rtspsrc
gst-inspect-1.0 avdec_h264
gst-inspect-1.0 v4l2src
```

## Python environment

### Linux

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

`--system-site-packages` makes the GI/GStreamer bindings installed by the
distribution package manager available inside the virtual environment.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

PyGObject and GStreamer must use the same architecture. Do not mix 64-bit
Python with a 32-bit GStreamer installation.

## Vehicle connection

Connection settings are available under **Settings > Backend > MAVLink** and
from the Telemetry/Autopilot/GPS menus in the bottom bar. Connect or Reconnect
first persists the edited values, then opens the backend with the new
endpoint.

```text
Serial: /dev/ttyUSB0         baud: 57600
UDP:    udp:0.0.0.0:14550
TCP:    tcp:127.0.0.1:5762
```

Enter Windows serial ports directly, for example `COM3`. Baud rate is not used
for UDP or TCP endpoints.

Opening a transport is not treated as proof of a healthy connection. Aircraft
state requires the target system/component heartbeat, video requires fresh
frames, and GPS requires a valid and recent fix.

## Video connection

Video settings are available under **Settings > Backend > Video** and from the
Video status menu in the bottom bar. Select the source type, URI or device,
codec, latency and frame timeout before connecting.

**Connect** or **Reconnect** persists the edited values before rebuilding the
GStreamer pipeline. Enable **Connect automatically when the application
starts** only for a source that is normally available. It is disabled by
default, so a missing USB camera or network stream does not start capture work
in the background.

The connection indicator becomes healthy only after fresh frames arrive.
Opening a pipeline without receiving frames is not reported as a connected
camera. If a camera temporarily outpaces the display, the backend continues
capturing while Qt renders only the newest frame, preventing delayed video
from accumulating.

## High-rate telemetry and UI rendering

MAVLink ingestion is independent from the display refresh rate. The backend
processes every received packet while the Qt adapter presents the newest
snapshot once per display frame. Old snapshots therefore cannot build up in
the GUI queue, and no telemetry-rate reduction is applied to the vehicle link.

The flight-dynamics display uses source timestamps and measured sample jitter
to render smooth intermediate frames. The map ignores unchanged telemetry,
keeps only the latest update while WebEngine starts, and reuses the existing
vehicle marker instead of rebuilding its SVG for every heading change.

## Map planning

The Once/Loop toggle at the start of the map toolbar controls whether the
uploaded waypoint route completes once or repeats continuously. It is
independent from ARM state, flight-mode selection and the explicit plan
transfer action.

The point-list button opens and closes the map dialog. Takeoff, waypoint,
loiter, RTL and land items can be reordered by their drag handles. Their
mission phase remains meaningful when the MAVLink item sequence is built.

The permanent `H` marker shows the flight controller's home/reference
position. Dragging it, or selecting **Set home/reference here** from the map
context menu, sends an acknowledged `MAV_CMD_DO_SET_HOME` command. The marker
is separate from plan overlays, so it cannot be deleted or cleared with the
plan.

Use `Ctrl+Z` to undo and `Ctrl+Shift+Z` to redo map edits. Point creation,
deletion, dragging, ordering, area edits and Once/Loop changes are restored
locally and are not transmitted until **Send Mission** is selected again.
Home changes are the exception: because they are direct vehicle commands,
their undo and redo steps also require an acknowledged home command.

The normal map context menu contains only GOTO, center-on-vehicle and
set-home/reference actions. Right-clicking an editable route point additionally
shows Delete. The map scale is displayed beside the zoom value, where it
remains visible independently from toolbar width.

HSS, QR and other server-owned reference layers are read-only. They are not
deleted with the user plan, included in plan archives or uploaded as ArduPilot
mission items.

## Resizable workspace

Drag the dividers to resize the sidebar, map/video panels, the visual and
diagnostic rows, or the log/parameter panels. Layout updates are deferred
during splitter movement to keep WebEngine, video and large tables responsive.
The frameless window can also be resized from every edge and corner. Splitters
and the window retain minimum panel sizes so operational controls cannot
collapse completely.

## Battery warning and failsafe

Two separate policies are available under
**Settings > Backend > Battery**:

- The warning policy uses either percentage or voltage. Crossing its threshold
  makes the bottom-bar battery indicator blink red.
- The failsafe policy has its own threshold and can be disabled. When enabled,
  it requests the configured flight mode only while the vehicle is armed and
  its own critical threshold has been crossed.

An alarm activation is logged once. While the alarm remains active, each
one-percentage-point battery change records an updated entry.

## Fake TEKNOFEST server

Run the simulator separately from the GCS:

```bash
python fake_teknofest_server.py
```

Defaults are `http://127.0.0.1:5000`, user `kirlangic`, password
`kirlangic-demo`, team number `1`, and English console logs. Select Turkish
console logs with `--lang tr`:

```bash
python fake_teknofest_server.py --lang tr
```

```bash
python fake_teknofest_server.py \
  --host 0.0.0.0 --port 5000 --competitors 27 --team-number 1
```

The server generates 27 competitors around the last accepted own-aircraft
position, four HSS areas, a QR coordinate and server time. HSS positions are
randomized once at startup, remain fixed for that server process, and stay
near the starting origin. Requests and responses appear in the server
terminal with `RX` and `TX` directions.

Own-aircraft telemetry is transmitted only when the vehicle connection,
position and aircraft/GPS time are fresh. A disconnected telemetry link never
forwards an old snapshot. Competitor, HSS and QR retrieval can continue under
their own server-connection lifecycle.

## Architecture boundaries

`PlannerService` owns GCS planning state and map reference data. It does not
execute missions, make autonomous decisions or run onboard algorithms. It
updates the operator plan atomically, separates source-owned data and provides
a consistent snapshot for transfer.

Primary responsibilities:

- `core`: product- and UI-independent events, dependency management and MAVLink
- `models`: data, validation and data-owned conversions
- `services/contracts`: implementation-independent service interfaces
- `services/vehicle`: `pymavlink`, telemetry and flight-controller parameters
- `services`: planner, commands, connections, competition and other backend services
- `ui`: Qt, Leaflet, widgets and visual state without business rules

Dependency direction:

```text
ui → services → models → core
core/mavlink → services/vehicle → service contracts
```

`DispatchCommandService` classifies an operator request and sends it to the
correct target. ArduPilot operations go through `VehicleService`; onboard
application operations go through their application channel. UI buttons never
construct raw MAVLink packets.

HSS, QR targets, safe areas and no-fly areas are not ArduPilot navigation
commands. They are not converted into `MISSION_ITEM_INT` without an explicit
translator or onboard consumer.

## MAVLink rules

`MavlinkMessageRouter` is the only reader of the MAVLink transport. A new
service must not open a second `recv_match()` loop.

- Consume messages through `VehicleService.subscribe()` or `client.subscribe()`.
- Capture a router sequence boundary before sending a request.
- Pass every send operation through the shared send lock.
- Confirm ARM, LAND and generic commands with `COMMAND_ACK`.
- Confirm mode changes from the new mode in `HEARTBEAT`.
- Confirm parameter writes from a matching `PARAM_VALUE`.
- Do not report a plan transfer as successful before completing the
  `MISSION_REQUEST(_INT)` and `MISSION_ACK` exchange.

If normal ARM fails, force ARM is attempted only after explicit operator
confirmation. It is never triggered automatically in the background.

## Using the backend without Qt

```python
from pathlib import Path

from src.models.mission import MapOverlay
from src.services.runtime import create_runtime_services

runtime = create_runtime_services(Path.cwd())
runtime.start()

runtime.facade.upsert_mission_item(
    MapOverlay(
        overlay_id="wp-1",
        overlay_type="waypoint",
        label="WP 1",
        points=[[39.86, 32.74, 120.0]],
        color="#74d99f",
    )
)

state = runtime.facade.mission_snapshot()
runtime.stop()
```

Consumers that need continuous data can subscribe to service events:

```python
runtime.planner_service.changed.subscribe(on_planner_changed)
runtime.telemetry_service.updated.subscribe(on_telemetry_changed)
```

## Extension guide

Start by deciding which component owns the data and the decision. The UI
collects input and displays results; connectivity, validation and workflow
remain in the backend.

### Adding a telemetry field

1. Add the field to `TelemetrySnapshot` with its canonical unit and unknown
   value behavior.
2. Decode the MAVLink message only under `services/vehicle`.
3. Keep separate freshness timestamps when the value combines several
   messages.
4. Send the updated model, not a raw MAVLink message, through the UI adapter.
5. Do not repeat information already visible in another operator panel.
6. Test conversion, missing messages and packets from another system ID.

### Adding a vehicle command

1. Define its code and parameters through `CommandRequest`.
2. Add routing to `DispatchCommandService`.
3. Extend the `VehicleService` contract before adding a missing capability.
4. Keep MAVLink constants, target IDs and ACK filters in `services/vehicle`.
5. Add declarative UI input to `ui/config/commands.py`.
6. Show success only after an ACK or confirmed vehicle state.
7. Test acceptance, rejection, timeout and connection loss.

### Adding an onboard task trigger

Target-lock or kamikaze algorithms do not belong in a GCS service.

1. The UI catalogue emits only the task request.
2. `DispatchCommandService` routes it to the onboard application channel.
3. The Jetson/Orin application starts the algorithm and returns an ACK.
4. Its result returns to the GCS through a dedicated result model.
5. TEKNOFEST transmission uses only results with a verified onboard source.
6. A disconnected GCS must not display success or fabricate a result.

### Adding map data

1. Use `MapOverlay` or `MapContact` for points, areas and live contacts.
2. Define geometry, editability and transfer behavior in `ui/config/map.py`.
3. Set `metadata.source` and `read_only` on externally owned items.
4. Replace source data atomically with `replace_source_overlays()` or
   `replace_contacts()`.
5. Mark non-vehicle items explicitly as `mission_upload: false`.
6. Test deletion, archiving, concurrent updates and source isolation.

### Adding a setting

1. Add the persistent value to the correct `backend` or `ui` group in
   `models/settings.py`.
2. Validate type and range in the model.
3. Add the field to `ui/config/settings_schema.py`.
4. Add the same key to `ui/i18n/en.json` and `ui/i18n/tr.json`.
5. If it changes runtime behavior, call the service's `configure()` method
   from the settings subscription in `runtime.py`.
6. Test migration, missing files and invalid values.

### Adding a backend service

1. Define its interface under `services/contracts`.
2. Identify its models and events.
3. Put the main service directly in `services/<service>.py`; protocol-specific
   telemetry and parameter adapters belong under `services/vehicle`.
4. Pass dependencies explicitly through the constructor.
5. Instantiate concrete implementations only in `create_runtime_services()`.
6. Register the service against its contract in the dependency container.
7. Add lifecycle ordering to `RuntimeServices` when required.
8. Expose only a narrow external API through `BackendFacade`.
9. Add a Qt signal in `ui/adapters` only when the UI needs the event.
10. Include a hardware-free contract test and an error-path test.

### Integration checklist

- `core` contains no PyQt6, domain service or project-specific dependency.
- `models` do not import services or UI code.
- Top-level services do not import `core.mavlink` directly.
- A physical MAVLink connection has exactly one reader.
- The UI does not show success before backend confirmation.
- Stale vehicle data is not forwarded after a disconnect.
- External map data cannot erase the operator plan.
- A real action creates a structured log; a no-op button press does not.
- The complete test suite and the relevant hardware smoke test pass.

## Logs and localization

Backend events are written to `data/logs/yki.jsonl`. Vehicle identity,
connection transitions, parameter changes, plan transfer, video-pipeline state
and confirmed command results remain available even when the UI is closed.

The log panel provides text search and level filtering. New records follow the
bottom only while the operator is already at the bottom; reviewing older
entries is not interrupted. Structured records are translated at display time
without mutating the stored raw message.

Interface text lives in `src/ui/i18n/en.json` and `src/ui/i18n/tr.json`. Both
packages must contain exactly the same keys. Update them together; invalid
JSON, missing keys and cross-language fallback fail the test suite.

TEKNOFEST HTTP traffic is logged both in the GCS and the fake-server console
with direction information. Diagnose behavior from backend actions and
correlation details rather than relying only on a visible UI message.

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests -v
```

The suite covers architecture boundaries, dependency lifecycle, MAVLink
routing and filtering, parameter confirmation, concurrent planning,
TEKNOFEST communication, battery policies, localization, GStreamer pipeline
generation, map history, video auto-connect, high-rate telemetry and video
coalescing, splitter behavior, frameless resizing and offscreen Qt startup.
The 1.0-beta release preparation suite contains 152 tests.

Release history is maintained in the [changelog][changelog-url].

## License

Copyright © 2026 TahsinCr.

This project is licensed under the GNU General Public License version 3 only
(`GPL-3.0-only`). Source code, the same license, and existing copyright and
license notices must accompany distributed copies and derivative works. See
[LICENSE](LICENSE) for the complete terms and [COPYRIGHT](COPYRIGHT) for the
copyright notice.

<!-- Badge images -->

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white
[contributors-shield]: https://img.shields.io/github/contributors/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[forks-shield]: https://img.shields.io/github/forks/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[stars-shield]: https://img.shields.io/github/stars/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[issues-shield]: https://img.shields.io/github/issues/Kirlangic-Team/ground-control-station.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/Kirlangic-Team/ground-control-station.svg?style=for-the-badge

<!-- Project links -->

[python-url]: https://docs.python.org/3.11/
[contributors-url]: https://github.com/Kirlangic-Team/ground-control-station/graphs/contributors
[forks-url]: https://github.com/Kirlangic-Team/ground-control-station/network/members
[stars-url]: https://github.com/Kirlangic-Team/ground-control-station/stargazers
[issues-url]: https://github.com/Kirlangic-Team/ground-control-station/issues
[license-url]: https://github.com/Kirlangic-Team/ground-control-station/blob/main/LICENSE
[changelog-url]: https://github.com/Kirlangic-Team/ground-control-station/blob/main/CHANGELOG.md
[lang-tr-url]: https://github.com/Kirlangic-Team/ground-control-station/blob/main/README-TR.md
