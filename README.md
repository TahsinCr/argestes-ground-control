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
- Correlated Onboard task control, mission state, QR recovery and remote logs
- Shared, typed YKİ–Onboard contracts without a second MAVLink connection
- Tabbed quick commands and Start/Restart/Cancel controls for onboard tasks
- Takeoff, waypoint, loiter, RTL, land and local safety-area planning
- HSS-aware P1/P2 Kamikaze approach planning with derived recovery geometry
- Live parameter/default and flight-plan synchronization from the controller
- RTSP, HTTP/MJPEG, RTP/UDP, V4L2, file and custom GStreamer sources
- Optional persistent video auto-connect with fresh-frame health validation
- TEKNOFEST server communication, competitor telemetry, HSS and QR references
- Searchable, level-filtered and runtime-translated structured logs
- Independent battery warning and optional failsafe thresholds
- Undo/redo history for map planning and acknowledged home changes
- Resizable map, video, log and parameter workspaces
- Persistent connection and backend settings

## Supported environments

YKİ is designed for current desktop operating systems that provide Python
3.11 or newer, Qt 6 and the required media and serial drivers. Arch Linux is
the team's primary hardware-validated environment; other Linux distributions,
Windows and macOS can run the application when their platform-specific serial,
GStreamer and video-capture dependencies are installed. Always validate the
actual serial device and video source on the machine used with flight hardware.

| Environment | Recommended version | Notes |
|---|---:|---|
| Arch Linux | Current | Primary development environment |
| Ubuntu | 24.04 LTS | Package names below target this release |
| Windows | Windows 11 x64 | Use the MSVC x86_64 GStreamer build |
| macOS | Current Intel or Apple Silicon release | Install native-architecture Python and GStreamer packages |
| Other desktop systems | Current supported release | Requires Python 3.11+, Qt 6, GStreamer and accessible serial/video devices |
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

Clone the repository together with its reusable core submodule:

```bash
git clone --recurse-submodules \
  https://github.com/Kirlangic-Team/ground-control-station.git
cd ground-control-station
```

For an existing checkout, synchronize the configured URL and initialize the
pinned core revision before installing or running YKİ:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

The core source is maintained independently at
[`TahsinCr/vehicle-autonomy-core`](https://github.com/TahsinCr/vehicle-autonomy-core).
The parent repository pins an exact core commit for reproducible builds.

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

## Shared core workflow

`src/core` is checked out at the exact revision recorded by the GCS repository.
Normal YKİ work should leave that directory untouched. To move YKİ to a newer
reviewed core revision:

```bash
git -C src/core fetch origin
git -C src/core checkout <core-commit>
git status --short
```

The parent diff should show `src/core` as one Git-link change. Core source
changes, tests and release notes belong in the core repository; only the
resulting commit pointer belongs in this repository. Run both test suites
before staging that pointer.

If `src.core` cannot be imported after switching branches, initialize the
submodule again instead of installing a copied core package:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

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

The right side of the bottom bar separates measured MAVLink packet delivery
from calculated RFD radio health. Its compact view shows RX/TX packet counts,
RF quality and transmit-buffer availability. Hover **Command/Telemetry** for
local/remote RSSI, noise, SNR, radio error correction and packet-loss details;
hovering the percentage or counters does not change their live-state styling.

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

The compact video meter reports quality, bitrate, FPS and resolution. Hover
**Video Stream** for the complete source, codec, frame-age, latency and timeout
details. The lower-right controls can crop-to-fill, fit the complete frame or
stretch the complete frame across the panel without cropping.

## High-rate telemetry and UI rendering

MAVLink ingestion is independent from the display refresh rate. The backend
processes every received packet while the Qt adapter presents the newest
snapshot once per display frame. Old snapshots therefore cannot build up in
the GUI queue, and no telemetry-rate reduction is applied to the vehicle link.

The flight-dynamics display uses source timestamps and measured sample jitter
to render smooth intermediate frames. The map ignores unchanged telemetry,
keeps only the latest update while WebEngine starts, and reuses the existing
vehicle marker instead of rebuilding its SVG for every heading change.

Map updates use a persistent QWebChannel path and are applied at most once per
browser animation frame. Parameter snapshots and log bursts are also batched,
while the parameter table updates existing rows in place. Backend MAVLink
ingestion and command acknowledgements remain independent from these display
limits. Moving contacts use incremental trail updates, and auxiliary network
waiters share one application-owned asyncio runtime instead of occupying the UI
thread or creating a polling thread per service.

## Map planning

The Once/Loop toggle at the start of the map toolbar controls whether the
uploaded waypoint route completes once or repeats continuously. It is
independent from ARM state, flight-mode selection and the explicit plan
transfer action.

The point-list button opens and closes the map dialog. Takeoff, waypoint,
loiter, RTL and land items can be reordered by their drag handles. Their
mission phase remains meaningful when the MAVLink item sequence is built.
Route segments show compact distance labels at useful zoom levels. Editable
points expose small radius and altitude fields in their context menu; values
are committed only when that editor closes.

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

The transfer group keeps mission upload and vehicle download separate. **Send
Mission** uploads the local operator plan; **Get Mission** imports the current
flight-controller mission without deleting local archive files or live
TEKNOFEST reference layers.

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
collapse completely. On Linux/X11 the application uses its own bounded resize
path when a window manager accepts but does not begin Qt's native resize; other
platforms retain compositor-managed resizing.

## Battery warning and failsafe

Two separate policies are available under
**Settings > Backend > Battery**:

- The battery display can use Pixhawk's reported percentage or estimate a 4S
  LiPo percentage from voltage. Full voltage, reserve voltage and the percentage
  represented by that reserve point are configurable; the zero-percent voltage
  is calculated automatically. Median and asymmetric time filters reduce brief
  motor-load sag without hiding sustained discharge.
- The warning policy uses either percentage or voltage. Crossing its threshold
  makes the bottom-bar battery indicator blink red.
- The failsafe policy has its own threshold and can be disabled. When enabled,
  it requests the configured flight mode only while the vehicle is armed and
  its own critical threshold has been crossed.

An alarm activation is logged once. While the alarm remains active, each
one-percentage-point battery change records an updated entry.

Click the battery readout in the bottom bar to open the compact battery editor.
The estimate is intended for display; Pixhawk voltage thresholds remain the
authoritative flight-safety protection when current sensing is unavailable.

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

`src/core` is a Git submodule. Core changes must be committed and tested in
the core repository first; YKİ then updates only the pinned submodule commit.
Do not copy shared core files back into the parent repository.

`src/communication` is the pinned SIHA Communication Contracts submodule. It
owns transport-neutral packet names, typed payloads, direction rules and
ground/onboard gateway behavior. It does not own MAVLink, sockets or vehicle
services. YKİ and SIHA Onboard must pin compatible revisions so task aliases,
ACK payloads, HSS updates, Kamikaze messages and remote logs stay identical.

Backend service contracts expose the core `EventBus`. Its normal service
policy isolates a failed observer so telemetry, video or state producers keep
running. Flows whose acknowledgement depends on local delivery must opt into
`ErrorPolicy.RAISE`; Orin remote-log ingestion does this before advancing its
sequence cursor.

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

The vehicle adapter and optional Orin application channel deliberately share
one `MavlinkClient` and one router. Core's `MavlinkRuntime` is the convenient
default for standalone tools; YKİ keeps explicit ownership because the Orin
application channel can be enabled or reconfigured without replacing the
flight-telemetry connection.

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

## SIHA Onboard v0.2-beta link

YKİ uses Vehicle Autonomy Core v1.3.1's application channel over the existing
MAVLink connection. It does not open another serial port, UDP socket or receive
loop. `V2_EXTENSION` uses network ID `77`, message type `42000`, YKİ system ID
`255`, onboard system ID `1` and onboard component ID `191`. The application
heartbeat runs once per second and expires after 10 seconds. These values are
available under the Onboard/Orin settings.

Core v1.3.1 bounds incomplete application-packet assemblies and uses one
thread-safe, latest-biased MAVLink async queue. Application packets and remote
log records are deeply immutable at runtime, while their serialization methods
still return detached `dict` and `list` values. YKİ decodes `logs.push` from
that detached wire representation before validation and acknowledgement.

`OnboardMissionService` sends `task.start`, `task.pause`, `task.resume`,
`task.stop`, `task.cancel`, `mission.status`, `mission.list`, `mission.events`
and `chain.start` through that shared channel. Replies are correlated strictly
by packet/request ID. A `system.ack` only means the command was accepted;
`mission.result` is the final mission outcome. Error, timeout and transport-loss
states remain separate, and a timed-out `task.start` is never retried
automatically.

The Kamikaze operator action asks for confirmation before sending `KAMIKAZE`.
If the ACK contains `waiting_for_mode=true`, the panel tells the operator to
select GUIDED. Phase comes from `mission.transition`. The Tasks tab exposes
Start/Restart and Cancel: restarting waits for a successful cancel ACK before
sending the next `task.start` request. YKİ does not automatically select GUIDED
when a task starts or AUTO when it ends; flight-mode changes remain explicit
operator actions.

Only tasks implemented on both sides are presented to the operator. Task
Start/Restart/Cancel controls remain disabled until a fresh Orin application
heartbeat is available; an open serial port alone is not treated as Onboard
readiness. Detailed mission ID, phase, progress, actuator step and Kamikaze QR
feedback is shown in **Vehicle Management > Task Actions**.

Selecting `TARGET_LOCK` starts the canonical `target.lock` mission through the
shared contract. Immediately before launch, GCS sends its current estimated
TEKNOFEST server time to onboard; the mission is not launched if no valid server
clock is available. This keeps the lock completion timestamp independent of the
vehicle's local system clock. The 5 Hz onboard `target.lock.status` atomically feeds the
official `iha_kilitlenme`, target centre, width and height fields into periodic
competition telemetry; if status stops for one second, the stale target box is
cleared before another server submission. The reliable `target.lock.report` is
accepted only from the onboard link and its `kilitlenmeBitisZamani` and
`otonom_kilitlenme` fields are posted unchanged to `/api/kilitlenme_bilgisi`.
Onboard receives no ACK before the HTTP POST succeeds, and a repeated
`report_id` never produces a second POST.

QR data is recovered from three sources:

1. live `kamikaze.event` notifications (`QR_DECODED` and
   `QR_DECODED_DURING_PULL_UP`);
2. `mission.result.snapshot.result.qr_text`;
3. cursor-based `mission.events` reconciliation after reconnect.

QR content remains inert plain text. It is associated with the active Kamikaze
mission, deduplicated and written to the central log. Conflicting live/result
values are preserved and reported. QR recovery remains a backend responsibility
and is not duplicated in the compact task selector.

Onboard `logs.push` requests are validated in batches of at most 16 records.
The complete batch enters the central log repository before ACK; session and
sequence cursors prevent duplicate rows on retry. Secret-like detail fields are
masked. Qt drains log events in bounded batches so they cannot monopolize the
UI thread.

Video remains independent from MAVLink. Configure the HDMI capture card as a
USB or GStreamer pipeline source. The video service retains only the latest
bounded frame, reconnects without blocking task controls and reports an
unhealthy stream when frames stop arriving. Actual negotiated resolution is
checked on caps changes and every five seconds; measured frame rate is derived
from received frames and published once per second.

Hardware verification checklist:

1. Connect YKİ to Pixhawk/RFD and verify ordinary telemetry first.
2. Enable Onboard and verify system `1`, component `191`, network `77` and
   message type `42000` in settings.
3. Start Onboard v0.2-beta and verify version, RTT and the one-second heartbeat
   without a second MAVLink endpoint.
4. Start Kamikaze, verify ACK only indicates acceptance, select GUIDED and watch
   `starting → running`.
5. Start `TARGET_LOCK`, verify target fields enter ordinary telemetry and one
   lock-result POST is produced after the four-second lock.
6. Inject both QR variants, reconnect once, and verify live, result and history
   recovery in the UI and log panel.
7. Retry an identical `logs.push` batch and verify it is ACKed without another
   log row.
8. Connect the HDMI capture card at 1080p/60, interrupt the signal and verify
   video reconnect while MAVLink controls remain responsive.

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
5. Register infrastructure in `infrastructure_dependencies.py`, shared vehicle
   adapters in `vehicle/dependencies.py`, or application behavior in
   `application_dependencies.py`.
6. Register the concrete type and its contract through one factory; do not
   resolve the global container from inside a service.
7. Add the resolved service to `RuntimeServices` and its lifecycle order only
   when it owns active resources.
8. Expose only a narrow external API through `BackendFacade`.
9. Add a Qt adapter or signal only when the UI actually consumes the event.
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
python run_tests.py
```

The runner reports the YKİ, pinned Communication Contracts and Core suites as
one result. It currently runs **434 tests** covering architecture boundaries,
dependency composition, shared contract validation, vehicle/Orin transport
sharing, Onboard task and cancel flows, P1/P2 Kamikaze approach planning,
mission transfer/history, QR and remote logs, TEKNOFEST communication,
GStreamer statistics, high-rate UI coalescing and Core's dependency, event,
mission and MAVLink internals. The suites remain independently owned even
though the root runner presents one final status.

For Linux runtime measurements, start YKİ through the performance monitor:

```bash
python monitor_performance.py --interval 1 --details-every 10
python monitor_performance.py --csv performance.csv
```

Use PSS for the closest estimate of the application's real memory cost; RSS is
also shown for comparison. The periodic breakdown separates the YKİ process
from Qt WebEngine renderer and zygote processes, which is useful when profiling
map interaction.

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
