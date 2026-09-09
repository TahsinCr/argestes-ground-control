# **Change Log**
All notable changes to this project will be documented in this file.

## Unreleased

### Fixed

* Wired the MAVLink connection controller to its `CONNECTION` child logger.
  Recovery attempts no longer terminate because a raw logging service receives
  the child-logger event signature.

## **[1.6.1-beta] - 06.09.2026**

### Added

* Added an application-owned MAVLink connection controller. It owns the
  initial automatic connection and bounded heartbeat recovery without creating
  another MAVLink reader, router or transport.
* Added focused regression coverage for initial connection policy, recovery
  backoff, manual-disconnect suppression, connection cancellation and video
  share worker generations.

### Changed

* Automatic MAVLink connection now has explicit settings semantics:
  `auto_connect` permits only the initial application-start attempt and
  `autoreconnect` permits retries after an established connection is lost.
  When both are disabled, the application makes no automatic connection
  attempt.
* Runtime startup and shutdown now own the connection controller lifecycle so
  recovery work cannot reopen a transport while the application is stopping.
* Connection UI operations are coalesced per channel and profile editing is
  temporarily disabled while a connection operation is pending.
* Video-share interface discovery is cached and refreshed outside the UI
  thread. Publisher pipelines use bounded leaky queues so a slow receiver
  cannot accumulate frames or memory indefinitely.
* Telemetry decoding now updates its internal snapshot without an unnecessary
  public defensive-copy round trip for every MAVLink packet.
* Connection menus distinguish unavailable optional backends from failed
  connections, preventing inactive compatibility surfaces from starting
  meaningless reconnect attempts.

### Fixed

* Stopping MAVLink now interrupts an in-progress heartbeat wait. This prevents
  a late connection from appearing after shutdown or after an operator has
  explicitly disconnected.
* Recovery backoff remains stable across the transport's internal stop event,
  and an operator disconnect immediately cancels any recovery attempt.
* Reconfiguring a video share invalidates and stops the previous publisher
  generation; an old worker can no longer overwrite the state of a newer one.

### Verification

* `python run_tests.py` passes **352/352** tests.
* `python -m compileall -q src tests` and `git diff --check` pass.

## **[1.6-beta] - 02.09.2026**

The **"Common Vehicle Platform and Multi-Camera Update"** turns `main` into a
clean, vehicle-agnostic ground-control foundation. SIHA-specific automation is
removed from the common runtime while MAVLink, planning, video and operator
workflows become more reusable.

### Added

* **Capability-aware vehicle control**:
  * Added transport-neutral command definitions and vehicle capability models.
    The UI now derives available commands and flight modes from the connected
    vehicle instead of assuming one airframe or autopilot workflow.
  * Added serialized command dispatch and latest-wins navigation revisions, so
    a superseded GOTO cannot be sent after a newer target has been selected.
* **Multi-camera operation**:
  * Added named camera input profiles, source-tagged frames/statistics and one
    capture service per required source.
  * Added 1, 2, 3 and 4 pane camera-wall layouts, fixed floating controls and
    direct source swapping from the camera-name menu. One source cannot appear
    in more than one pane.
  * Added multi-target video sharing with selectable camera/panel source,
    codec, transport, network interface, fixed-FPS and timestamp settings.
* **Composition and compatibility**:
  * Centralized application composition in `src/container.py` with explicit
    infrastructure, vehicle, application and runtime registration order.
  * Added passive UI compatibility adapters while the common UI is redesigned;
    they preserve visual construction without opening legacy connections.

### Changed

* **Common backend boundary**:
  * Removed SIHA-specific Onboard/Orin execution, TEKNOFEST competition,
    target-selection, Kamikaze approach and archive runtime ownership from
    `main`.
  * Kept the shared MAVLink vehicle stack, camera services, planner,
    parameters, logs, map cache, mission archive and battery safety features
    as common infrastructure.
* **Settings and models**:
  * Migrated legacy single-camera and single-telemetry settings into validated
    profile collections while retaining backwards-compatible active views.
  * Added battery percentage-source selection between Pixhawk reporting and
    voltage estimation.
* **Operator interface**:
  * Reworked the video panel for compact split-screen operation, source names,
    stable bottom toolbars and non-blocking source switching.
  * Simplified panel chrome, compacted the left sidebar, restored clear
    ARM/DISARM quick actions and improved log-search placement, status colors
    and translated labels.
  * Updated the map, telemetry and command panels to consume generic vehicle
    capabilities without binding their presentation to SIHA automation.
* **Repository maintenance**:
  * Removed the fake TEKNOFEST server, performance monitor and tests that only
    exercised removed competition/onboard automation.
  * Replaced obsolete application/infrastructure dependency modules with the
    single composition root and focused hardware-free contract coverage.

### Fixed

* Prevented large window sizes from expanding the left sidebar until the
  operator explicitly drags its splitter.
* Kept camera controls attached to the video wall rather than individual tiles
  while changing layouts.
* Kept video source reconfiguration outside the UI thread and coalesced rapid
  profile updates to the latest requested configuration.

### Documentation

* Rewrote the README for the common GCS architecture, current command/video
  model, development boundaries and platform requirements.

### Testing

* `python run_tests.py` passes **345/345** YKİ, Communication Contracts and
  Vehicle Autonomy Core tests.

## **[1.5.0-beta] - 22.08.2026**
The **"Shared Onboard Contracts and Mission Planning Update"** brings the
Kamikaze approach planner from pull request #1 into the main operator workflow,
adopts the shared SIHA communication contracts and makes task and map feedback
more dependable during live operation.

### Added
* **Kamikaze approach planning**:
    * Added the P1/P2 approach selector merged through pull request #1, with
      derived dive and pull-up geometry, HSS-aware alignment checks and a
      reusable backend service independent from the map UI.
    * Added operator-facing approach documentation and automated geometry,
      clearance, fallback and selection tests.
* **Shared communication contracts**:
    * Added `siha-communication-contracts` as the pinned `src/communication`
      submodule and routed Onboard task, mission, Kamikaze, HSS and remote-log
      messages through its typed models and `ContractGateway`.
    * Kept MAVLink framing, request correlation and the single transport reader
      in Vehicle Autonomy Core; the contracts package opens no connection of
      its own.
* **Map workflow tools**:
    * Added readable distance labels between navigation points and a separate
      toolbar action for downloading the current flight-controller mission.
    * Added compact point radius/altitude editing from the context menu and
      complete undo/redo coverage for creation, deletion and archive loading.

### Updated
* **Task controls and feedback**:
    * Limited the operator task list to the connected Kamikaze and actuator-test
      workflows and added task-aware Start, Restart and Cancel behavior.
    * Moved detailed task feedback into the Vehicle Management task-actions
      tab, with compact mission ID, phase, step, QR and progress presentation.
    * Task controls now require a live Orin application heartbeat. Idle tasks
      no longer appear to have started, and disconnected programmatic actions
      cannot bypass the disabled controls.
* **Map interaction**:
    * Improved point dragging so edit handles cannot remain behind, kept route
      distance labels synchronized and made trackpad zoom use the stable map
      center.
    * Preserved TEKNOFEST/HSS reference layers and local mission archives while
      applying undo, redo and vehicle-download changes.
* **Camera telemetry**:
    * Renamed the panel to Camera and removed fixed resolution metadata from
      its subtitle.
    * Negotiated resolution is verified every five seconds and on caps changes;
      measured FPS is published once per second from received frame timing.

### Fixed
* Preserved cancellation requests across task start/reconciliation phases and
  treated an already-inactive Onboard task as a successful terminal cancel.
* Corrected Kamikaze target synchronization and shared-contract task aliases
  without opening a second MAVLink connection.
* Prevented map drag artifacts, incomplete point-history entries and premature
  context-editor logging.
* Prevented task status cards and controls from reporting readiness while the
  Orin application heartbeat is unavailable.

### Testing
* The combined runner passes **434 tests** across YKİ, Communication
  Contracts and Vehicle Autonomy Core.
* Coverage includes the P1/P2 approach planner, shared contract direction and
  payload rules, Onboard request/cancel flow, mission download and history,
  compact task UI behavior, negotiated video resolution and one-second FPS
  publication.

<br>

## **[1.4.4-beta] - 21.08.2026**
The **"Async Runtime and Operator Performance Update"** moves auxiliary I/O
off the interface thread, reduces map traffic under live telemetry and keeps
flight-mode authority with the operator.

### Added
* **Shared asynchronous runtime**:
    * Added one application-owned asyncio loop for backend waiters instead of
      creating independent polling threads for each service.
    * Added lifecycle-safe scheduling, cancellation and shutdown behavior for
      dependency-managed services.
* **Operator feedback**:
    * Added task-specific Kamikaze and actuator-test status cards with a shared
      mission progress indicator.
    * Added a third video presentation mode that stretches the complete frame
      to the panel without cropping it.

### Updated
* **Non-blocking backend I/O**:
    * Moved TEKNOFEST polling, connection health checks, Onboard reference
      synchronization and local tile serving onto the shared async runtime.
    * Kept blocking HTTP work outside the event loop and retained bounded
      request, reconnect and shutdown behavior.
* **Map hot path**:
    * Limited moving-marker rendering to a stable display cadence and changed
      contact trails to send incremental points instead of rebuilding complete
      paths for every telemetry sample.
    * Preserved the existing map appearance, controls and planning behavior.
* **Vehicle Autonomy Core v1.3.1**:
    * Updated the pinned submodule to the latest compatible release.
    * Adopted the core's expanded mission orchestration, parallel/background
      execution and reliability improvements without adding another MAVLink
      connection or receive loop.
* **Cross-platform runtime**:
    * Isolated POSIX serial-permission handling so Windows and macOS startup do
      not depend on Linux-only account modules.

### Changed
* **Flight-mode ownership**:
    * Starting an Onboard task no longer changes the vehicle to GUIDED.
    * Completing or cancelling a task no longer changes the vehicle to AUTO.
      Mode selection remains an explicit operator action.

### Fixed
* Coalesced repeated Onboard reference synchronization so slow links cannot
  accumulate stale work.
* Hardened asynchronous cancellation and shutdown paths to avoid orphaned
  timers, idle threads and late callbacks during application exit.
* Kept command acknowledgements and high-rate MAVLink ingestion independent
  from map, video and TEKNOFEST refresh work.

### Testing
* The combined runner passes **393 tests**: **242 YKİ tests** and **151 Vehicle
  Autonomy Core v1.3.1 tests**.
* Coverage includes shared-runtime lifecycle, async service scheduling,
  incremental map transport, video view modes, task progress and operator-owned
  flight-mode behavior.

<br>

## **[1.4.3-beta] - 13.08.2026**
The **"Core Concurrency Compatibility Patch"** release. This patch adopts
Vehicle Autonomy Core v1.3 and keeps the YKİ–Onboard application link
compatible with the core's stricter immutable packet model.

### Updated
* **Vehicle Autonomy Core v1.3**:
    * Updated the pinned submodule from v1.2 to the official v1.3 release.
    * Adopted serialized mission launches, monotonic queue timeouts and stricter
      mission completion phase checks.
    * Adopted shared sync/async dependency initialization gates so singleton
      and scoped services cannot be constructed twice during resolution races.
    * Adopted ordered event replay, thread-safe latest-biased MAVLink async
      queues and safer dispatcher shutdown from worker callbacks.
    * Adopted deeply immutable application packets, mission snapshots, results
      and remote-log records while retaining mutable serialization output.
* **Release identity**:
    * Updated the YKİ application heartbeat version to `1.4.3-beta`.

### Fixed
* **Onboard remote-log compatibility**:
    * Decode `logs.push` batches from the packet's detached wire
      representation. This preserves core's immutable runtime payload while
      supplying the JSON `dict`/`list` shape required by remote-log validation.
    * Kept remote-log acknowledgement, session/sequence deduplication and
      retry-after-storage-failure behavior unchanged.

### Compatibility
* Core v1.3 does not rename the public classes or methods used by YKİ.
* Vehicle telemetry, Onboard heartbeat, task requests, ACK correlation and
  remote logs continue to share one MAVLink client and router.
* The stricter `complete()` and stopped-channel behavior is internal to core;
  existing YKİ mission and connection lifecycles require no additional API
  adaptation.

### Testing
* The combined runner passes **341 tests**: **206 YKİ tests** and **135 Vehicle
  Autonomy Core v1.3 tests**.
* The Orin communication suite passes **16 tests**, including remote-log
  validation, acknowledgement, deduplication and delivery-retry behavior.

<br>

## **[1.4.2-beta] - 13.08.2026**
The **"Runtime and Map Performance Patch"** release. This update focuses on
keeping the operator interface responsive under live telemetry and map load,
while tightening the validity and persistence rules around backend data.

### Added
* **Runtime diagnostics**:
    * Added a single Python test entry point that runs the YKİ and pinned core
      suites as one result.
    * Added a Linux performance monitor with aggregate CPU, PSS and RSS
      reporting, per-process WebEngine breakdowns and optional CSV output.
    * Made the monitor tolerate application and renderer processes exiting in
      the middle of a `/proc` sample.

### Updated
* **Service boundaries**:
    * Consolidated service interfaces under `services/abstracts` and updated
      dependency composition without introducing another service layer.
* **Map and video hot paths**:
    * Bounded cursor updates and changed contact trails to append incrementally
      when possible instead of rebuilding the full polyline on every sample.
    * Reduced temporary video-stat allocations and retained negotiated frame
      rate information without accumulating historical peaks.
    * Moved tile-cache cleanup away from the UI thread, bounded tile reads and
      replaced repeated directory-size scans with incremental accounting.
* **Compact operator layout**:
    * Reduced the left sidebar footprint and let Autonomous Control use the
      available vertical space below Flight Telemetry.

### Fixed
* **Telemetry validity**:
    * GPS state now expires using source freshness, treats MAVLink sentinel
      values as unavailable and no longer presents stale coordinates as a
      healthy fix after disconnect.
* **TEKNOFEST client resilience**:
    * Separated session, telemetry, clock, QR and HSS endpoint health so one
      reference-data failure does not incorrectly drop an authenticated
      session.
    * Added bounded HTTP responses and a monotonic estimate of competition
      server time.
* **Persistence safety**:
    * Made settings updates atomic across concurrent read-modify-write calls
      and used same-directory temporary files with `fsync` before replacement.
    * Prevented mission archive paths from escaping the configured archive
      directory.
* **Web map safety**:
    * Escaped backend-provided map labels before inserting them into HTML.

### Testing
* The combined runner passes **331 tests**: **206 YKİ tests** and **125 Vehicle
  Autonomy Core v1.2 tests**.
* Coverage now includes process-exit sampling races, GPS freshness and MAVLink
  sentinels, TEKNOFEST endpoint isolation and clock handling, concurrent
  settings writes, archive confinement and bounded map hot paths.

<br>

## **[1.4.1-beta] - 13.08.2026**
The **"Core Reliability Patch"** release. This patch updates the shared
Vehicle Autonomy Core dependency without changing the operator workflow or the
single-connection MAVLink architecture.

### Updated
* **Vehicle Autonomy Core v1.2**:
    * Moved the pinned core revision from v1.1.1 to the official v1.2 release.
    * Adopted bounded MAVLink application-fragment assembly and bounded
      asyncio bridge queues so burst traffic cannot grow internal buffers
      without limit.
    * Adopted safer concurrent dependency scopes, serialized mission lifecycle
      callbacks and pause-aware mission execution timeouts.
    * Included the core fixes for event-wait races, one-shot MAVLink
      subscriptions and retryable asynchronous resource cleanup.
* **Release identity and documentation**:
    * Updated the YKİ application heartbeat version to `1.4.1-beta`.
    * Refreshed the English and Turkish integration notes and verified test
      counts while retaining Python 3.11 as the YKİ runtime target.

### Compatibility
* The existing `MavlinkClient`, router and physical transport remain shared by
  vehicle telemetry and the Onboard application channel. No second serial or
  UDP reader was introduced.
* Operator controls, wire settings and public YKİ service boundaries remain
  compatible with the v1.4-beta release.

### Testing
* The YKİ suite passes **194 tests** against the updated core dependency.
* Vehicle Autonomy Core v1.2 passes its **125 tests**, covering dependency,
  event, mission and MAVLink concurrency and lifecycle behavior.

<br>


## **[1.4-beta] - 13.08.2026**
The **"Responsive Operator Controls Beta"** release. This update focuses on
the controls used most often during operation: autonomous task handling,
compact-screen usability, reliable frameless resizing and a clearer quick
command layout.

### Added
* **Tabbed autonomous controls**:
    * Split the Autonomous Control panel into dedicated Quick Commands and
      Tasks tabs without changing the underlying command services.
    * Added independent, color-coded status rows for vehicle commands and
      onboard task operations.
    * Added an explicit task restart action. Restart first receives a successful
      cancel acknowledgement, then starts the selected task again; a rejected
      cancel never produces a second start request.

### Updated
* **Task operation flow**:
    * Reduced the task controls to the two actions supported by the operator
      workflow: Start/Restart and Cancel.
    * Kept actuator-test safety confirmation and routed its start request over
      the existing correlated Onboard application channel.
    * Removed the QR result control from the task selector while retaining QR
      recovery and structured logging in the backend.
* **Quick Commands presentation**:
    * Grouped flight controls and target profile inputs into separate compact
      cards with stable status feedback at the bottom of the tab.
    * Simplified the target profile to altitude and airspeed; loiter radius is
      no longer presented as a quick profile input.
* **Responsive map controls**:
    * Added bounded responsive sizing for map statistics, GPS status, layer
      selection and the floating tool dock.
    * Preserved readable icons and labels at narrow panel widths instead of
      shrinking controls below a practical operator size.
* **Shared core dependency**:
    * Updated the pinned Vehicle Autonomy Core revision from v1.1 to v1.1.1,
      adopting its clearer package-layout checks and standalone test runner.

### Fixed
* **Actuator task dispatch**:
    * Removed the stale capability gate that could leave Actuator Test visible
      but prevent the UI from sending `task.start` to a connected Onboard.
* **Frameless resizing**:
    * Fixed edge and corner resizing on Linux/X11 window managers that report a
      successful native resize request without actually starting the drag.
    * Widened the invisible resize target and retained native compositor resize
      behavior on Wayland, Windows and macOS.
* **Small-screen layout**:
    * Made both autonomous-control tabs independently scrollable and kept task
      actions and status visible at the bottom of the panel.
    * Lowered the safe application window minimum to 1152x648 while preserving
      minimum sizes for operational controls.

### Testing
* The YKİ suite passes **194 tests**, including task restart ordering,
  actuator dispatch, responsive Qt controls, frameless resizing and the
  existing MAVLink, map, video and telemetry regressions.
* Vehicle Autonomy Core v1.1.1 passes its **109 tests** through the new
  `src/core/tests/run.py` entry point.

<br>


## **[1.3-beta] - 11.08.2026**
The **"Onboard Operations and Performance Beta"** release. This update turns
the shared Orin channel into an operator-facing mission link, improves the
quality of battery and radio information, and removes the main UI bottlenecks
seen while telemetry, parameters, logs and map contacts arrive together.

### Added
* **Onboard mission client**:
    * Added correlated start, pause, resume, stop, cancel, status, list and
      event-history requests over the existing MAVLink application channel.
    * Added mission transition/result handling, GUIDED-waiting state, critical
      event reporting and a copyable QR result in the operator panel.
    * Recovered QR results from live Kamikaze events, terminal mission results
      and cursor-based mission history without treating an ACK as mission
      success.
    * Added an acknowledged TEKNOFEST target update before Kamikaze start. A
      missing or rejected target now blocks launch instead of starting with
      stale coordinates.
* **Voltage-based battery estimate**:
    * Added a display-only 4S LiPo estimator for installations where Pixhawk
      current sensing is not reliable. Median filtering and separate discharge
      and recovery time constants reduce motor-load voltage sag.
    * Added configurable full voltage, reserve voltage and reserve percentage;
      the zero-percent voltage is derived from that calibration automatically.
    * Added a compact battery popup in the bottom bar with the estimate profile,
      warning threshold and independent failsafe settings in one place.
* **Parameter defaults**:
    * Added an ArduPilot MAVFTP reader for `param.pck?withdefaults=1` on the
      shared MAVLink router, without opening another receive loop.
    * Added a read-only Default column to the parameter panel. Unsupported or
      unavailable defaults remain visibly unknown instead of using a catalogue
      fallback.
* **Link diagnostics**:
    * Added MAVLink packet-delivery, estimated loss and RFD local/remote radio
      counters to telemetry snapshots and connection details.
    * Added compact telemetry and video readouts for packet direction, RF/TX
      buffer, bitrate, FPS and resolution. Full diagnostics appear only when
      the operator hovers the connection name, leaving live values visually
      stable.

### Updated
* **Shared core dependency**:
    * Replaced the copied core tree with the independently maintained
      `TahsinCr/vehicle-autonomy-core` Git submodule and pinned the reviewed
      Vehicle Autonomy Core v1.1 revision.
    * Migrated backend contracts and services from the retired `EventStream`
      API to `EventBus`, including explicit error propagation for acknowledged
      remote-log batches.
    * Added clear startup guidance when the submodule has not been initialized,
      and documented the clone/update workflow in both project guides.
* **High-rate UI path**:
    * Moved map telemetry and state deltas to persistent QWebChannel signals and
      coalesced them to the newest browser animation frame.
    * Reused map markers, tooltips and trail arrays, bounded competitor history,
      and skipped unchanged mission freshness and tile-cache updates.
    * Batched Qt log delivery, updated full parameter catalogues incrementally,
      and coalesced parameter snapshots before touching table widgets.
    * Cached the active settings snapshot in the main window so high-rate
      telemetry no longer deep-copies the entire settings model per frame.
* **Parameter synchronization**:
    * Published large ArduPilot catalogues in bounded progress batches and kept
      a healthy transfer alive while new indexes continue to arrive.
    * Preserved staged edits while live values/defaults update and avoided
      rebuilding the thousand-row table for each incoming batch.
* **Remote onboard logs**:
    * Validated `logs.push` atomically, deduplicated by session/sequence and sent
      its ACK only after the complete batch reached the central YKİ log pipeline.
    * Masked secret-like detail fields and batched GUI insertion so onboard
      bursts do not block flight controls.
* **Test server reference data**:
    * Made the fake TEKNOFEST QR coordinate deterministic so target forwarding
      and Kamikaze integration can be repeated reliably.

### Fixed
* **Operator command responsiveness**:
    * Marked ARM, mode and other controls pending before starting their worker
      so rapid clicks cannot open duplicate command transactions.
    * Serialized operator command waits on the shared router and retried lost
      mode/`COMMAND_ACK` exchanges within the configured operation timeout.
    * Captured the ARM confirmation boundary before transmission so a fast
      heartbeat cannot be missed.
* **Parameter and log stalls**:
    * Removed repeated full-table and full-log rebuilds during sustained input.
    * Reset parameter/default synchronization state cleanly on disconnect and
      prevented an active transfer from being declared failed while it was
      still making progress over a slow radio.
* **Telemetry presentation**:
    * Used the router's measured MAVLink delivery percentage as the main link
      value while keeping calculated RFD health as a separate diagnostic.
    * Reset stale radio/error metrics on disconnect and kept tooltip ownership
      on the connection title instead of changing the live metric theme.
* **Map rendering drops**:
    * Avoided repeated SVG, tooltip, route-freshness and cache-count work when
      the underlying values have not changed.

### Testing
* The YKİ suite now passes **183 tests**, including application-channel
  correlation, Kamikaze target/QR recovery, remote logs, voltage estimation,
  parameter defaults, command retries and high-rate Qt/map regressions.
* The pinned core revision passes **109 tests** for dependency lifecycles,
  events, missions and MAVLink transport/application behavior.

<br>


## **[1.2-beta] - 02.08.2026**
The **"Unified Onboard Integration Beta"** release. This update establishes a
single, extensible MAVLink application path between YKİ, the flight controller
and the onboard computer, and moves application construction to explicit,
testable dependency containers.

### Added
* **Dependency Composition Roots**:
    * Added a reusable dependency container with singleton, transient and
      scoped lifetimes, constructor injection, explicit providers, priorities,
      child scopes, warmup and deterministic disposal.
    * Split YKİ registrations into infrastructure, vehicle, application and UI
      modules while retaining the existing service contracts and runtime
      lifecycle.
    * Added a backend application container and a UI child container so Qt
      consumes injected backend services without constructing them itself.
* **Extensible MAVLink Application Dispatch**:
    * Added a bounded application-message dispatcher and handler registry for
      future bidirectional Orin commands without adding protocol-specific
      branches to the vehicle service.
    * Added success/error response envelopes, request correlation, queue
      backpressure and isolated handler execution.
    * Added transport-safe structured remote-log records and bounded batches
      with protocol validation, sequencing and payload-size limits.
* **Reusable Mission Foundation**:
    * Added project-independent mission descriptors, lifecycle phases,
      priority bands, resource conflicts, prerequisites, queue policies,
      retries, chains, checkpoints, progress and structured mission events to
      the shared core.

### Updated
* **Shared Orin Link**:
    * Routed YKİ-to-Orin commands, acknowledgements and future application
      packets through the existing vehicle `MavlinkClient`; no second serial
      connection or competing MAVLink reader is opened.
    * Added application-level heartbeat, ping/pong, timeout, latency, packet
      counters and request/response correlation.
    * Increased Orin liveness probing to once per second while preserving the
      configurable health timeout.
    * Made Orin reconnect and disconnect manage only the application channel,
      leaving flight telemetry active.
* **Onboard Log Ingestion**:
    * Added handler registration and acknowledged `logs.push` ingestion with
      per-session sequence de-duplication.
    * Forwarded structured Orin records into the normal YKİ logging service so
      they are persisted and displayed through the existing log pipeline.
* **Runtime Construction**:
    * Replaced the monolithic manual service assembly in `runtime.py` with
      dependency modules while preserving service startup/shutdown ordering,
      settings propagation and the backend facade.
    * Injected battery policy and UI adapters through the composition roots
      instead of constructing backend behavior inside the main window.

### Fixed
* **UDP Output Discovery**:
    * Fixed `udpout:` connections timing out when a MAVLink UDP server waits
      for the GCS to advertise its return address before streaming telemetry.
    * Sent a standard GCS heartbeat immediately and at most once per second
      during connection discovery, while still requiring a real vehicle
      heartbeat before reporting success.
    * Kept serial, TCP, `udp` and `udpin` connection behavior unchanged and
      avoided additional sockets or background threads.
* **Orin State Reporting**:
    * Removed transport-only assumptions from Orin health reporting and tied
      connection state to real application-peer liveness and vehicle-link
      availability.
    * Prevented stale or unrelated system/component packets from being
      accepted as a healthy onboard application response.

### Testing
* **Integration and Regression Coverage**:
    * Added dependency lifecycle, container composition, Orin request routing,
      handler dispatch, remote-log acknowledgement/de-duplication and Qt
      injection coverage.
    * Added UDP discovery tests proving that `udpout` advertises the GCS before
      waiting and passive transports do not emit discovery traffic.
    * Verified the complete YKİ suite with **169 passing tests**.
    * Verified a real ArduRover UDP endpoint connects through the production
      YKİ connection class and supplies live `SYS_STATUS` telemetry.

<br>


## **[1.1-beta] - 28.07.2026**
The **"Reliable Mission Workflow Beta"** release. This update improves mission
editing history, prioritizes operator-initiated MAVLink transfers and removes
avoidable delays while restoring archived plans to the map.

### Updated
* **Mission Transfer Coordination**:
    * Gave operator-initiated mission uploads priority over automatic
      flight-controller mission synchronization.
    * Allowed an incomplete background mission download to yield promptly
      without reporting an operator-triggered interruption as a transfer
      failure.
* **Map Rendering Pipeline**:
    * Batched archived overlay changes so route geometry and mission counters
      are recalculated once after the complete plan is restored.
    * Avoided redundant settings persistence and full-interface restyling when
      a loaded archive already uses the active map layer.
* **Mission Diagnostics**:
    * Added archive-load log details for item count, repeat mode and base map
      layer.

### Fixed
* **Map Edit History**:
    * Preserved undo and redo history when a queued planner snapshot confirms
      the map's own local mutation.
    * Fixed newly added waypoint, takeoff, land, RTL and area items remaining
      on the map after `Ctrl+Z`.
* **Mission Archive Loading**:
    * Removed repeated route and mission-stat calculations while archived
      points and areas are inserted into Leaflet.
    * Prevented unchanged map-layer settings from rewriting the settings file
      and triggering unnecessary UI work.
* **ArduPilot Mission Progress**:
    * Accounted for ArduPilot's sequence-zero home record when displaying the
      active mission item and total mission count.
    * Fixed the first executable mission item being presented as item two in
      the flight telemetry panel.

### Testing
* **Mission Regression Coverage**:
    * Added concurrent MAVLink coverage proving that an operator upload
      preempts an incomplete automatic mission download.
    * Added planner coverage ensuring a preempted refresh preserves the local
      mission plan without producing a false failure.
    * Added regression coverage for queued map history synchronization,
      batched map rendering, no-op settings updates and ArduPilot mission
      sequence normalization.

<br>


## **[1.0-beta] - 25.07.2026**
The **"Operational Ground Control Beta"** release. This release turns the
initial modular foundation into a configurable, bilingual and high-rate
operator application while keeping vehicle decisions and protocol work in the
backend.

### Added
* **Bilingual Interface Infrastructure**:
    * Replaced the Python translation catalogue with validated `en.json` and
      `tr.json` language packages.
    * Added runtime translation for structured backend logs and connection
      status details without changing the original stored log record.
    * Made fake-server console logs English by default with optional Turkish
      output through `--lang tr`.
    * Added language-package parity, JSON validation and translation-key tests.
* **Battery Warning and Failsafe Policies**:
    * Added an independently configurable visual warning threshold by
      percentage or voltage.
    * Added a separate optional failsafe threshold and mode request.
    * Added a blinking bottom-bar alarm, one-time activation logging and
      renewed logging for each one-percent battery change.
* **Map Planning Controls**:
    * Added a distinct Once/Loop toggle to the map toolbar with state-specific
      icons and route closure feedback.
    * Added a point-list dialog that can be toggled from the toolbar.
    * Added drag-and-drop ordering with dedicated handles for route and local
      map items.
    * Added responsive spacing for the three map toolbar groups.
    * Added a permanent draggable `H` home/reference marker and context-menu
      placement backed by acknowledged `MAV_CMD_DO_SET_HOME`.
    * Added bounded map-edit history with `Ctrl+Z` undo and `Ctrl+Shift+Z`
      redo for route points, areas, ordering, repeat mode and acknowledged home
      changes. Restored plan edits remain local until the operator transfers
      the plan again.
* **Operator Diagnostics**:
    * Added log search and level filtering while preserving the operator's
      manual scroll position.
    * Added local NED, improved flight dynamics, body-axis acceleration,
      battery current/consumption and additional flight readouts.
* **Operator Workspace Controls**:
    * Added a vertical splitter between the visual workspace and the
      log/parameter workspace.
    * Added smooth, non-opaque resizing for the sidebar, map/video and
      log/parameter splitters.
    * Added reliable edge and corner resizing for the frameless application
      window.
* **Video Connection Policy**:
    * Added a persistent video auto-connect option to the video connection
      popup and backend settings.
    * Kept automatic camera startup disabled by default so unavailable devices
      do not create background capture work.
* **Application Identity and Licensing**:
    * Added the Kırlangıç application logo from `src/assets`.
    * Added the official GNU GPL v3.0-only license and the TahsinCr copyright
      notice.

### Updated
* **Service Layout**:
    * Replaced the mission service naming with `PlannerService`, reflecting
      that YKİ plans and dispatches but does not execute autonomous missions.
    * Flattened unnecessary `catalogs`, `orchestration` and `processing`
      service directories.
    * Moved top-level planner, command and connection services directly under
      `services`, and vehicle telemetry/parameter implementations under
      `services/vehicle`.
    * Removed built-in parameter catalogue values; the parameter view remains
      empty until values arrive from the connected flight controller.
* **Map and Mission Semantics**:
    * Preserved takeoff, waypoint, loiter, RTL and land phases when ordering
      and uploading vehicle mission items.
    * Kept HSS, QR, server-owned areas and other reference overlays outside
      editable or archived vehicle plans.
    * Generated fake-server HSS positions once after the first valid ownship
      telemetry, keeping them stable for that process and 280–900 metres from
      the aircraft's initial reported position.
* **Settings and Runtime Data**:
    * Moved generated runtime data out of version control.
    * Added safe, complete default settings creation when `data/settings.json`
      is missing, unreadable or invalid.
    * Separated battery warning and failsafe thresholds in the persistent
      settings model and schema.
    * Applied connection-popup changes, including video auto-connect, directly
      to persistent settings before Connect or Reconnect.
* **Documentation**:
    * Split the project guide into an English `README.md` and Turkish
      `README-TR.md`.
    * Standardized first-party source comments and Python docstrings in
      English while keeping operator-facing text in the language packages.
    * Updated installation, architecture, extension, operation, logging,
      localization and testing guidance for the 1.0 beta layout.

### Fixed
* **High-Rate UI Performance**:
    * Kept MAVLink ingestion at full speed while coalescing GUI delivery to the
      latest snapshot per display frame.
    * Coalesced camera delivery to the newest available frame, keeping capture
      throughput independent from Qt rendering while bounding latency and
      memory use.
    * Bounded each queued Qt event-dispatch batch so bursts of backend events
      cannot monopolize the GUI thread.
    * Replaced permanent idle adapter polling with event-driven queued Qt
      delivery and tracked short-lived operation workers.
    * Limited WebEngine telemetry rendering to the latest map state at display
      cadence while retaining full-rate backend telemetry processing.
    * Removed duplicate flight-dynamics repaints, repeated Qt stylesheet
      recalculation and unchanged telemetry label updates.
    * Prevented duplicate map telemetry calls and startup script backlogs.
    * Reused the Leaflet vehicle marker DOM instead of rebuilding its SVG icon
      for every heading update.
    * Debounced map-cache counters and avoided repeated cache sorting and
      unchanged signal-meter repaints.
* **Runtime Stability**:
    * Made SIGINT/SIGTERM shutdown non-reentrant and stopped backend services
      exactly once through the Qt event loop.
    * Added explicit WebEngine, animation, timer, subscription and worker
      shutdown before widget destruction.
    * Bounded local tile-server concurrency and ensured its server thread
      terminates when caching is disabled or the application closes.
    * Allowed the map to fall back to direct tile-provider URLs when the local
      cache server cannot bind a socket.
    * Replaced per-row log widgets with a shared paint delegate and reduced
      synchronous disk flush pressure without weakening final/error durability.
    * Moved the durable log flush boundary to runtime shutdown, avoiding
      periodic filesystem stalls on telemetry or GUI threads.
    * Reduced connection-health polling overhead while retaining heartbeat and
      freshness-based status transitions.
    * Rate-limited identical offline TEKNOFEST errors while preserving immediate
      reporting when the failure reason changes.
    * Prevented small negative AGL and ground-speed sensor noise from causing
      rejected TEKNOFEST telemetry and repeated session reconnects.
* **Map Interaction**:
    * Fixed the point-list button so a second click closes the dialog.
    * Fixed the Once/Loop selection reverting before mission transfer.
    * Fixed toolbar group spacing at narrow and wide panel sizes.
    * Separated RTL from the normal waypoint path and rendered its terminal
      return to the permanent home marker as a distinct dashed route.
    * Restored the point-only delete context menu on both markers and labels,
      and improved point dragging with frame-coalesced document-level tracking.
    * Preserved externally managed HSS/QR layer order across unchanged refreshes
      and batched moving-contact map updates into one WebEngine delivery.
    * Polled HSS independently at 1 Hz, removed stale regions after an empty
      response and accepted later phase lists without redrawing unchanged
      regions.
    * Fixed the permanent home/reference marker jumping to `(0, 0)` after a
      rejected drag operation and preserved acknowledged positions against
      stale autopilot home telemetry.
    * Moved the map scale readout beside the zoom value so it remains visible
      when the toolbar occupies the lower edge.
    * Reduced the map context menu to GOTO and centering actions, exposing
      Delete only for editable points and Set Home only for the map position.
    * Made undo/redo restore each completed drag as one operation and kept
      server-managed overlays outside local history.
* **Window and Panel Resizing**:
    * Fixed frameless-window edge resizing on environments where native system
      resizing is unavailable.
    * Coalesced manual resize events and deferred expensive splitter layouts
      until release, preventing stutter while resizing the window or panels.
* **Telemetry Presentation**:
    * Improved continuous flight-dynamics interpolation using real source
      timestamps and measured sample jitter.
    * Preserved high-rate telemetry while avoiding stale Qt event queues.
    * Prevented repeated connection-health object creation for every MAVLink
      packet; health remains monitored by the watchdog.
* **Logs and Battery State**:
    * Fixed automatic log scrolling so new entries follow only when the view
      is already at the bottom.
    * Changed log level filtering to a minimum-severity threshold, so the
      selected level and every more critical level remain visible.
    * Preserved operation-specific command details in localized logs,
      including the requested flight mode and mission item count, repeat mode,
      item-type breakdown, ignored local items and failure reason.
    * Fixed battery warning and failsafe thresholds being treated as one shared
      value.

### Testing
* **Expanded Coverage**:
    * Added tests for battery alarms and failsafe behavior, JSON localization,
      fake-server HSS stability, map controls, log filtering and scroll
      behavior, settings recovery and high-rate Qt telemetry coalescing.
    * Added a 5,000-snapshot burst test proving that the GUI consumes the newest
      frame without replaying stale telemetry.
    * Added burst coverage for video-frame coalescing, video auto-connect,
      frameless resizing and horizontal/vertical splitter behavior.
    * The complete 1.0-beta release-preparation suite contains **152 tests**.

<br>


## **[0.1-beta] - 22.07.2026**
The **"Modular Ground Control Foundation"** release. This first beta establishes a UI-independent backend, a reusable MAVLink core, verified vehicle operations, centralized mission data, real connection health reporting, GStreamer video support, and a local TEKNOFEST communication simulator while preserving the established Kırlangıç Team YKİ interface.

### Added
* **Layered Application Architecture**:
    * Added reusable `core`, domain-focused `models`, contract-driven `services`, and isolated `ui` layers.
    * Added a single runtime composition root and constructor-based dependency injection.
    * Added a stable backend facade for future web, CLI, automation, and alternative UI clients.
* **Reusable MAVLink Core**:
    * Added endpoint parsing, connection lifecycle, message envelopes, filters, bounded cache/history, a single-reader router, and asyncio-compatible channels.
    * Added native MAVLink type, source system/component, message ID, condition, and predicate filters.
    * Added serialized sending and sequence-bounded request/response waits to prevent stale ACK reuse.
* **Vehicle and Mission Services**:
    * Added heartbeat watchdogs, telemetry decoding, full parameter synchronization, verified parameter writes, and mission upload/download.
    * Added takeoff, waypoint, loiter, RTL, land, safe-zone, no-fly-zone, HSS, and target map models.
    * Added explicit mission transfer controls independent from ARM/DISARM and flight-mode changes.
* **Connection and Telemetry UI**:
    * Added status popups with persisted reconnect settings, timeout controls, packet counters, and verified active/inactive states.
    * Added compact GPS, RF link, battery, video transfer, and flight telemetry indicators without duplicating panel data.
* **Video Pipeline**:
    * Added UI-independent GStreamer pipelines for RTSP, HTTP/MJPEG, RTP/UDP, V4L2/USB, files, and custom sources.
    * Added frame-based connection health, FPS, bitrate, resolution, fit/fill controls, and reconnect handling.
* **TEKNOFEST Simulator**:
    * Added a standalone fake HTTP server with authentication, server time, HSS and QR coordinates, and 27 independently sampled nearby competitor aircraft.
    * Added directional request/response logging and realistic telemetry sample age reporting.

### Updated
* **Mission Map Workflow**:
    * Centralized map changes in revisioned `MissionState` snapshots so UI and background writers share one authoritative state.
    * Separated user-owned mission overlays from server-owned HSS, QR, and live competitor data.
    * Preserved marker positions during middle-mouse panning and protected non-user overlays from edit, delete, and archive operations.
* **Command Safety**:
    * Converted vehicle actions to acknowledgement-driven state changes instead of optimistic button state.
    * Added operator-confirmed force ARM only after a normal ARM failure.
    * Clarified ARM/DISARM, mode selection, and mission upload as separate operations.
* **Settings System**:
    * Split persistent backend and UI settings while retaining automatic migration from the legacy flat format.
    * Moved repeated settings, commands, and map tools to declarative schemas.
* **Documentation Layout**:
    * Consolidated architecture, extension, operation, and MAVLink guidance into one standalone README.
    * Removed obsolete architectural drafts, sample mission files, packaged source archives, and standalone deployment leftovers from the main branch.

### Fixed
* **Connection Accuracy**:
    * Fixed false-positive TEKNOFEST and video connection states.
    * Fixed telemetry health reporting so the active endpoint is measured from current MAVLink traffic and RF data when available.
    * Fixed stale vehicle telemetry being forwarded after the physical link becomes unavailable.
* **Vehicle Controls**:
    * Fixed unstable ARM/DISARM display state and immediate mode rollback before vehicle confirmation.
    * Fixed mission numbering and mission transfer feedback.
    * Fixed LAND naming and ArduPlane-compatible mode behavior.
* **UI Reliability**:
    * Fixed recursive Qt signal updates that could terminate the application with `RecursionError`.
    * Fixed invalid `DRI_PRIME=0` startup handling.
    * Improved responsive telemetry layout and panel scrolling on smaller displays.

### Documentation
* **Standalone Project Guide**:
    * Added installation, connection, video, simulator, architecture, data-flow, backend API, MAVLink, extension, logging, and testing documentation to `README.md`.
* **Release History**:
    * Rebuilt the changelog from scratch for the Kırlangıç YKİ project and established the `0.1-beta` release format.

### Testing
* **Architecture and Unit Coverage**:
    * Added tests for dependency boundaries, event isolation, settings migration, mission concurrency, MAVLink routing, vehicle commands, parameters, competition traffic, and logging.
* **UI and Integration Coverage**:
    * Added Leaflet/style integrity checks, Qt offscreen smoke tests, connection popup tests, mission controls, and GStreamer pipeline verification.
* **Release Verification**:
    * The complete local suite passes with **95 tests** before creating the `v0.1-beta` release tag.

<br>
