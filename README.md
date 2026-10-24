# Argestes Ground Station

[![Python 3.11+][python-shield]][python-url]
[![License: GPL-3.0-only][license-shield]][license-url]

**English** · [Türkçe](README-TR.md) · [Changelog](CHANGELOG.md)

Argestes Ground Station (AGS) is a modular desktop ground control station for
monitoring vehicles, planning missions and working with live camera feeds. Its
configurable workspace lets operators choose the panels they need, while the
extension system gives developers a clear way to add integrations.

MAVLink is the current vehicle protocol. Maps, cameras, joystick control and
competition-server connections live in separate extension packages. The shared
application provides the workspace, settings, logging and extension lifecycle.

[Installation](#installation) · [Vehicle connection](#vehicle-connection) ·
[Workspace](#workspace) · [Extensions](#extensions) ·
[Extension development](#extension-development) · [Troubleshooting](#troubleshooting)

## What you can do

* Monitor vehicle attitude, telemetry, battery and connection health.
* Plan map-based missions, inspect parameters and use the active vehicle's
  available commands.
* Arrange panels side by side or in tabs, save different workspace views and
  show or hide panel groups with one action.
* Work with multiple camera feeds, separate recordings and live video outputs.
* Configure joystick axes, PWM ranges and button actions for RC control.
* Connect to SIHA Teknofest servers and retain a local history of exchanges.

Available panels and controls depend on which extensions are enabled and which
capabilities the active vehicle provider supplies.

## Installation

### Requirements

The application requires Python 3.11 or newer, Git and Qt 6. Development and
hardware integrations are primarily exercised on Linux; device access, video
plugins and window-manager behavior depend on the host system.

Install the root requirements and those of the extensions you intend to use.
The MAVLink extension adds `pymavlink` and `pyserial`; Map & Autonomy adds Qt
WebEngine. Camera support also needs native GStreamer components, described below.

### Set up a checkout

Run these commands from a terminal:

```bash
git clone --recurse-submodules https://github.com/TahsinCr/argestes-ground-control.git
cd argestes-ground-control

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r extensions/mavlink/requirements.txt
python -m pip install -r extensions/map_autonomy/requirements.txt

python main.py
```

Vehicle Autonomy Core is included as a Git submodule. If you already have a
checkout without it, run `git submodule update --init --recursive` before starting
the application. Keep Qt and Qt WebEngine versions aligned, and use the same
Python environment for installation and launch.

### Camera dependencies

The Camera extension uses GStreamer 1.0 through PyGObject. Install the runtime,
Python bindings and plugins needed by your video sources and codecs. These are
system-dependent requirements and are not supplied by the root Python
requirements file. The bindings must be importable from the application's
virtual environment.

Local recording requires a supported NVIDIA or VA-API hardware encoder exposed
as a GStreamer element. Having a GPU alone is not sufficient: its driver and
encoder plugin must also be available. Network streaming can fall back to a
software encoder; local recording reports an error if no supported hardware
encoder is found.

## Vehicle connection

Open the vehicle connection control in the bottom bar, create a named profile
and enter the endpoint used by your vehicle or telemetry router. Choose the
serial baud rate when using a serial device.

| Connection | Example endpoint | Meaning |
| --- | --- | --- |
| Serial | `/dev/ttyUSB0` | Open a local serial device; set its baud rate separately. |
| UDP input | `udpin:0.0.0.0:14550` | Listen on local port 14550 on all interfaces. |
| UDP output | `udpout:192.168.1.30:14550` | Send to a remote host and port. |
| TCP | `tcp:127.0.0.1:5762` | Connect to a TCP server. |

For UDP input, the vehicle or router must send telemetry to the ground
station's reachable address and listening port. `0.0.0.0` is a local bind
address, not a remote destination. A UDP output profile needs a peer that can
respond; choosing that mode alone does not establish a telemetry session.

A healthy MAVLink connection requires an incoming vehicle heartbeat. The
connection profile and the MAVLink settings have distinct roles:

* `auto_connect` permits the initial connection attempt at application startup.
* `autoreconnect` permits recovery after an established connection drops.
* An explicit disconnect suppresses recovery until the next connection request.

The **MAVLink** settings category contains dialect and protocol options.
**Vehicle Connection** owns profiles, connection controls and link-quality
indicators. GPS and autopilot indicators report protocol status; they do not
open connection editors.

## Workspace

### Arrange panels

Use **View → Panels** to show or hide individual panels. Move the pointer over
a panel to reveal its handle and menu. Drag the handle toward another panel's
edge to split the space, or toward its center to create tabs. The destination
preview shows where it will be placed.

The adjacent menu provides placement, visibility and content-size locking.
There is no separate layout-edit mode. Panels in the same split share the
relevant dimension; unrelated layout branches can be resized independently.
A size lock is saved with the view and can raise the window's minimum size.

### Save views and panel categories

The upper part of the activity bar selects saved workspace views. Each view
keeps its own panel visibility, arrangement, dimensions and categories.
Switching views changes the workspace without resizing the application window.
Older single-layout files migrate to an initial saved view.

Use the **+** button below the view list to create a view or category. A new view
asks for a name and icon, then opens an empty workspace. Add its panels through
the View menu. Right-click an existing view to edit its name or icon, or delete
it; the last remaining view cannot be deleted.

Categories appear in the lower activity-bar section and belong to the active
view. To create one, choose its name and icon, select the visible panels to
include, and confirm with **Add**. A category toggles its members together
without controlling the underlying extension services.

Panel visibility and extension activation are separate controls. Closing a
camera panel, for example, does not disable the Camera extension.

### Display preferences

Light and dark themes are available in Settings. The **Vehicle Telemetry**
category lets you choose and order 58 fields, switch between a list and grid,
use one to four columns and group fields by category.

## Extensions

Open the extension manager using the button next to Settings in the bottom bar.
Search and filters help find installed packages and inspect their activation
and dependency state. Configure each package in its own category in the shared
Settings window.

The following packages are included in this repository. Defaults apply to a
new configuration; saved activation preferences take precedence.

| Package | Responsibility | Default |
| --- | --- | --- |
| [MAVLink](extensions/mavlink/) | Vehicle protocol, decoded data, commands, GPS and autopilot status. | Enabled |
| [Vehicle Connection](extensions/vehicle_connection/) | Connection profiles, lifecycle, recovery and link indicators. | Enabled |
| [Map & Autonomy](extensions/map_autonomy/) | Map, mission planning, planner API and tile cache. | Enabled |
| [Battery](extensions/battery/) | Battery display, estimation and alarms. | Enabled |
| [Parameters](extensions/parameters/) | Parameter inspection and editing through `mavlink.parameters`. | Enabled |
| [Vehicle Dynamics](extensions/flight_dynamics/) | Attitude, speed and altitude instruments. | Enabled |
| [Vehicle Telemetry](extensions/telemetry_display/) | Configurable telemetry readouts. | Enabled |
| [Quick Commands](extensions/quick_commands/) | Commands and modes supplied by the active vehicle provider. | Enabled |
| [Camera](extensions/camera/) | Camera feeds, connection controls, recording and live outputs. | Disabled |
| [Joystick](extensions/joystick/) | RC calibration, channel mapping and button actions. | Disabled |
| [SIHA Teknofest Server](extensions/siha_teknofest_server/) | Server profiles, telemetry delivery and SQLite history. | Disabled |

### Activation and reloads

The host checks declared capabilities before applying activation changes.
Packages that can be changed independently, including Camera and Vehicle
Connection, can be enabled or disabled while the application runs. Disabling a
package removes its services and UI contributions but retains its settings.

Settings changes mark the affected package for reload. A provider with active
dependents cannot be reloaded independently, and at least one vehicle protocol
provider must remain enabled. The application currently ships a MAVLink
provider; another protocol needs its own implementation and required adapters.

### Cameras, joystick and server tools

Camera connection controls appear in the bottom bar when Camera is enabled.
Each source can have its own recording or network output. The camera-panel
record action controls camera recordings together, while output settings allow
individual targets to be enabled or disabled. Recordings use fixed 30 FPS
output, pause when their source becomes stale and resume in the same file when
frames return. Stop recording normally to finalize the MP4.

Joystick provides device selection, RC channel assignments, PWM calibration,
deadzone, expo, reversal and button actions. Its current RC adapter supports
MAVLink. Deactivating joystick control releases RC overrides.

SIHA Teknofest Server opens its profile manager from the bottom bar rather than
as a workspace panel. Profiles support separate HTTP connections; the package
submits normalized telemetry and records exchanges in SQLite. Requests begin
when an operator connects or a profile is configured for automatic connection.

## Architecture

### Shared application and Core

[Application composition](src/container.py) connects shared services and the
extension host. `src/` owns workspace behavior, configuration, session logging
and normalized vehicle data contracts.

[Vehicle Autonomy Core](src/core/) is a separate repository included as a
submodule. Both the application and extensions may import its public
`src.core` packages directly. The Extension API handles integration with the
application; it does not wrap or restrict access to Core.

### Protocol and feature boundaries

[The Extension API](src/extension/api/) defines contributions and provider
contracts. [The host](src/extension/host/) discovers manifests, checks
dependencies, composes services and manages their lifecycle.
The host also shares a small clock-reference registry through the extension
context. Sources keep stable IDs separate from their display titles, allowing a
feature such as camera timestamping to default to local system time, use UTC, or
select an optional server clock without depending on that server extension's
implementation. A source that has not supplied a valid time sample remains
visible in settings but cannot be selected until it becomes available.

Protocol packages decode their messages into shared data and expose supported
operations. Feature packages consume those contracts or keep protocol adapters
inside their own package. MAVLink's shared client and router own transport
reading; UI widgets do not create competing readers.

`VehicleService` provides the common vehicle interface without requiring every
provider to implement RC or missions. Optional `RcControl` and `MissionControl`
contracts describe those operations separately. Parameters currently require
`mavlink.parameters`; Map & Autonomy requires `vehicle.navigation`.
The map package also owns [PlannerMapApi](extensions/map_autonomy/api/planner.py),
its web assets and cache lifecycle.

## Extension development

### Configuration validation and clocks

An extension may ship `settings.schema.json` beside its manifest. The shared
settings service validates nested values before saving, including writes from
custom settings pages and legacy model projections. The supported vocabulary
is `type`, `properties`, `items`, `required`, `enum`, `minimum`, `maximum`, and
`additionalProperties`. This is a deliberately limited declarative schema,
not a full JSON Schema implementation. Unknown configuration keys remain
preserved unless the package explicitly rejects them. Specialized editors and
model validation retain responsibility for cross-field rules and protocol
semantics. Existing profile formats and settings pages do not need to change.

Use `context.clock_references.publish(key, title, provider)` to publish clocks.
The host binds the owner; a local key produces a stable `<extension>.<key>` ID.
The same API instance can read all available sources, but can modify or remove
only its owner's sources. Private Python members are not a sandbox boundary.

The [queue probe](tools/README.md) provides reproducible Qt delivery measurements.

### Package and manifest

Create an importable package under `extensions/<identifier>/`, with an
`__init__.py`. Use a lowercase
identifier with underscores; it must match the directory name. Add a
`manifest.json` declaring its entry point, API major version, required
capabilities and provided capabilities.

A minimal manifest looks like this:

```json
{
  "identifier": "example_tool",
  "name": "Example Tool",
  "description": "An operator tool",
  "category": "Tools",
  "version": "1.0.0",
  "api_major": 2,
  "entry_point": "extensions.example_tool.plugin:create_extension",
  "requires": [],
  "provides": ["tools.example"],
  "default_enabled": false,
  "settings": {}
}
```

Provide `create_extension()` in `plugin.py`. Its returned object implements
`configure(registrar, context)` and may implement `start(context)` and `stop()`.
Use configuration to declare contributions, startup for runtime work, and
shutdown to release workers, timers, subscriptions and connections.

The current Extension API is major version 2. Manifests must declare
`api_major: 2`; providers must implement the current clock-sample and parameter
synchronization contracts. Extensions targeting earlier API majors must be
updated before they can be loaded.

### Services and UI contributions

| Registration | Purpose |
| --- | --- |
| `registrar.singleton(...)` | Register a service and its dependencies; duplicate ownership is rejected. |
| `registrar.visual_view(...)` | Add a panel to the common workspace. |
| `registrar.status_widget(...)` | Add an ordered connection or metric indicator to the bottom bar. |
| `registrar.settings_page(factory)` | Supply a settings page that loads when its category is selected. |

`context.settings` contains the package configuration; `context.services`
resolves shared services. Obtain logging through those services so the host can
attach the extension identity automatically. Settings pages may implement
`prepare_shutdown()` to release their own resources.

Keep widgets and assets in their owning package. Provide
`i18n/en.json` and `i18n/tr.json` catalogs with namespaced keys; the application
validates and caches merged catalogs. Do not reuse another package's keys.

Declare the capabilities your package actually needs. Supporting another
vehicle protocol may require a new adapter, but should not require changes to
unrelated feature panels. The [Joystick plugin](extensions/joystick/plugin.py)
and [Vehicle Connection plugin](extensions/vehicle_connection/plugin.py) show
existing service and UI integration patterns.

## Configuration and stored data

| Location | Contents |
| --- | --- |
| `data/settings.json` | Application preferences, extension configuration and activation state. |
| `data/workspace-layout.json` | Saved views, panel arrangements, visibility, sizes and categories. |
| `data/logs/gcs_<session-date>_<session-time>.json` | Session log shared by the application and extensions. |
| `data/teknofest/history.sqlite3` | SIHA Teknofest Server exchange history. |

Each extension stores `enabled` and `config` under
`extensions.<identifier>` in the shared settings file. Disabling it preserves its
configuration. Legacy extension files and renamed identifiers are migrated
while retaining unknown settings and disabled-state preferences.

Extension log records include `extension_id`. Use the session log to correlate
connection events, lifecycle changes and package errors. Treat local settings,
credentials and operational logs as machine-specific data when sharing a
checkout or reporting an issue.

## Development and verification

Install the dependencies needed by the features under test, then run:

```bash
python run_tests.py
```

This runs the application's and Core's `unittest` suites together and selects
Qt's offscreen platform by default. The real-map smoke test uses a separate Qt
process for WebEngine cleanup.

Software tests cover contracts and UI behavior; vehicle links, mission transfer,
camera encoders and joystick devices still need verification with the intended
hardware. See the [changelog](CHANGELOG.md) for recorded changes and verification
results.

## Troubleshooting

### The map reports that WebEngine is unavailable

Install `extensions/map_autonomy/requirements.txt` in the environment used to
launch `main.py`. A system Python installation and `.venv/bin/python` can have
different Qt packages. Check that Qt and Qt WebEngine versions match.

### A UDP profile receives no telemetry

Check the profile's input/output direction, listening port and destination
address. For an input profile, the sender must target the ground station,
and the relevant interface and firewall must admit that traffic. Another
process bound to the same port can also interfere with reception.

### A panel or extension is missing

First check the extension's activation and dependency state. Then check
**View → Panels** and the active saved view. A hidden panel and a disabled
extension are different states. GPS and autopilot indicators are status-only;
connection editing belongs to the vehicle connection control.

### Recording cannot find an encoder

Check the GStreamer runtime, PyGObject import in the launch environment and
hardware encoder plugins. Local recording requires a supported hardware
encoder and does not silently fall back to CPU encoding.

### The checkout reports a missing Core module

Run `git submodule update --init --recursive` from the repository root. Core's
contents are not included in a checkout that omits its submodule.

## License

Argestes Ground Station is licensed under [GPL-3.0-only](LICENSE).
Copyright © 2026 TahsinCr. See [COPYRIGHT](COPYRIGHT) for attribution information.

[python-shield]: https://img.shields.io/badge/Python-3.11%2B-blue
[python-url]: https://www.python.org/downloads/
[license-shield]: https://img.shields.io/badge/License-GPL--3.0--only-blue
[license-url]: LICENSE
