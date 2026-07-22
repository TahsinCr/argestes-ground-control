# **Change Log**
All notable changes to this project will be documented in this file.

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
