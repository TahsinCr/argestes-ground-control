# **Change Log**
All notable changes to this project will be documented in this file.

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
