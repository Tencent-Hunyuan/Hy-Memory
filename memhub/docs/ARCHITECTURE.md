# MemHub / One Memory Architecture

Updated: 2026-10-16. This document describes the current MemHub implementation.
Hy Memory is the SDK and engine. MemHub is the product and release name;
One Memory / `one-memory` remains in historical module, storage, service, and
compatibility interface names.

## Product and Runtime Shape

MemHub is a local-first memory product. A user
authorizes supported AI conversation sources, keeps their original content as
local Evidence, processes eligible content through Hy Memory, governs the
result, and grants hosts a separate scope for recall.

```text
AI hosts and local conversation files
  ├─ historical import / incremental sync
  ├─ Host hooks, plugins, Skills and MCP
  └─ authorized recall
             │
             ▼
      Python Core / FastAPI
      ├─ source adapters and grants
      ├─ Evidence and import jobs
      ├─ durable memory_write_jobs
      ├─ per-job memory_tasks execution records
      ├─ model-content permission checks
      ├─ Hy Memory bridge
      ├─ Governance and retrieval
      └─ local HTTP API
          ▲        ▲         ▲
          │        │         └─ Host Bridge / recall broker
       Web UI    CLI / Runtime Control
                    ▲
                 Electron
```

There is one managed Runtime and one MemHub data home per installation. CLI and
Electron are clients and lifecycle interfaces to that Runtime; they do not own
separate memory stores or extraction engines. The Web UI is served or developed
as a client of Core and does not access the database or host files directly.

## Workspace Ownership

| Path | Responsibility |
| --- | --- |
| `apps/core` | Python 3.12 / FastAPI data plane: SQLite business data, source adapters, Evidence, import and memory jobs, HY integration, Governance, retrieval, permissions, and API. |
| `apps/web` | React / TypeScript product UI and API client. |
| `apps/desktop` | Electron shell, native window and OS integration; attaches to or starts the managed Runtime. |
| `apps/cli` | Bootstrap, install, upgrade, service lifecycle, diagnostics, and user-facing commands. |
| `packages/runtime-control` | Runtime discovery and lifecycle, local installation transactions, service management, host setup control, and shared CLI/Desktop runtime contracts. |
| `packages/host-bridge` | Loopback recall broker and host-facing MCP, Skill, Hook, and plugin assets. It is not a second data plane. |
| `packages/contracts` | Shared TypeScript API contracts, aligned with Core's API models. |
| `packages/operations` | Shared typed operation flows used by clients. |
| `scripts` | Development, privacy, test, packaging, and release gates; not runtime product code. |

Python dependencies and Core tests are managed by `uv`; the TypeScript
workspace is managed by `pnpm`. The repository requires Node.js 24 and Python
3.12. See the root `package.json`, `apps/core/pyproject.toml`,
[`../README.md`](../README.md), and [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
for executable commands and contribution rules.

## Data and Processing

### Source authorization and synchronization

Source root authorization and Harness synchronization are distinct:

- SQLite `source_grants` records the authorized root, its identity, grant and
  revocation state. A grant does not by itself turn synchronization on.
- Core-owned `<data_dir>/config.json` is the durable desired state for
  `sync_enabled` per source. The embedded Hy Memory SDK is part of MemHub and
  may use the complete local memory store; there is no second model-content
  permission switch.
- Core is the only writer of `config.json`. It validates a strict schema and
  persists updates through a private temporary file, `fsync`, atomic replace,
  and directory `fsync`.
- Web, Desktop, CLI, and Runtime call Core APIs; they do not edit the file or
  keep a second durable copy of these settings.

Turning sync on requires an authorized source root, prepares catch-up import
work, commits the desired state, then activates scanning. Historical complete
turns that do not yet have a durable write receipt are added to
`memory_write_jobs`. Startup performs the same reconciliation for sources that
are enabled in `config.json`.

Turning sync off has two effects for that Harness: scanning stops, so future
content creates no new write job; and the drain stops claiming its pending
jobs. A job that was claimed but has not made its first HY/model call is
released back to `pending`. A job that has already started its first HY/model
call is allowed to finish the whole job. Previously published memories remain
available for authorized recall.

Periodic polling and optional filesystem watch live in Core. The Web UI does
not schedule scans. Historical import and live notifications converge on the
same source identity, Evidence, and import-job model.

Adapters may read authorized host files, including a host application's own
SQLite database. They do not write MemHub's database and do not call HY
directly. Source readers retain path containment, root/file identity,
symlink-refusal, and cursor/anchor checks.

### Memory processing

The normal content path is:

```text
authorized source
  -> Evidence
  -> import job / complete conversation turn
  -> memory_write_jobs
  -> persisted event-time window
  -> durable drain
  -> memory_tasks internal execution record
  -> sedimentation and source gates
  -> Hy Memory bridge
  -> write verification and Governance
  -> search / recall / UI
```

Evidence is stored before model work so processing failures do not discard
source content. Complete-turn preparation preserves user, assistant, and tool
roles and their authorship. Historical recovery, scanner callbacks, live
notifications, manual memory input, and Matter discovery all submit to the
same durable `memory_write_jobs` table. They do not invoke HY directly.

`memory_write_jobs` is the sole production intake and scheduling queue. Every
row receives the complete turn's `event_at`, a `time_quality`, and a persistent
monotonic `queue_seq`. The drain anchors a fixed ten-minute window at the
oldest eligible event time. Different Harnesses may run concurrently inside
that window, while each Harness remains FIFO by `queue_seq`; live priority
metadata never jumps the event-time window. The persisted anchor prevents a
restart or a chain of nearby jobs from sliding the window forward.

First onboarding imports are a barrier: accepted Harnesses finish importing
the shared history cutoff and submit their chunks before the drain begins
model work. Failed or explicitly disabled sources are excluded rather than
blocking the user's whole timeline. See
[`contracts/memory-event-time-scheduling-v1.md`](contracts/memory-event-time-scheduling-v1.md).

`memory_tasks` remains as an internal execution, lease, input, output, and
reconciliation record created while a claimed write job runs through HY. It is
not independently recovered or scheduled by the production composition root.
The legacy `ProcessingCoordinator` and supervisor backfill/resume scheduler are
not started by Core; history recovery writes durable jobs instead.

The synchronization API and the first HY-call boundary use the same
sedimentation/source locks. If pause commits first, the job cannot cross into HY and returns to
`pending`. If the job records its first HY invocation first, pause does not
interrupt that in-progress job. Lease expiry after a possible HY call remains
`unknown`, never a blind retry.

### HY and Governance

Hy Memory is the underlying extraction, memory, and semantic retrieval
framework. Product code enters it through `HyMemoryBridge` and the permission
transport, not by constructing an SDK client in feature code.

The bridge and Governance layer preserve Evidence references, authorship,
scope, write verification, revisions, corrections, and deletion behavior. A
successful SDK response alone is not sufficient evidence of a product-level
write. Retrieval results are checked against current Governance and access
scope before being returned to the UI or an authorized host.

The currently pinned SDK is the public PyPI distribution
`hy-memory==2.0.4`. Runtime configuration and permission boundaries are
documented in [`../MEMORY.md`](../MEMORY.md).

## Permission Boundaries

These permissions answer different questions and must not be collapsed:

1. **Source grant:** may Core read this selected host data root?
2. **Harness sync:** should Core continue discovering and importing changes
   from this source?
3. **Model-content permission:** may eligible local conversation content be
   sent to the configured model provider?
4. **Host recall grant:** may this external host retrieve specified memory
   scopes or use specified tools?

Model-content permission is a global final-state setting in `config.json`.
Tasks do not carry a parallel permission receipt or per-task model permission
ledger. A live permission guard rechecks the current setting and active model
profile at remote-send and publication boundaries. Revocation therefore cannot
be inferred from an old task snapshot.

Host installation and recall authorization can modify or expose configuration
in another process. Their preview, fingerprint, scope, credential, and rollback
protections remain because they cross an external trust boundary. These do not
turn Harness sync into a multi-step external operation.

The product must not depend on screen capture, Accessibility/AXTree, OCR,
keyboard/mouse, clipboard, or background audio collection.

## Storage

SQLite is the sole authoritative MemHub business database, under the user's
One Memory Home (normally `~/.one-memory`). It stores Evidence, grants, import
jobs and cursors, memory tasks, Governance, model profiles, and other product
state. Runtime versions and npm packages are not the data directory.

HY-owned vector, graph, cache, and history stores are managed according to the
HY SDK contract under the One Memory Home. They are not a second authoritative
MemHub business database. Adapters may separately read a host's own database
under the source grant.

MySQL-only business storage was an earlier proposal and is not the active
architecture. Decision D56 selected SQLite and superseded D53. Do not add a
MySQL runtime, fallback, dual-write path, or SQLite-to-MySQL migration as part
of ordinary development. Historical migration research and records may remain
in dated documents, but they are not implementation instructions.

`config.json` contains desired user settings, not runtime health, source-root
grants, cursors, or job state. Runtime state is reconstructed from durable
settings and database state.

## Runtime and Interfaces

The repository has one Runtime implementation, one default data home, and one
operating-system service identity per installation. The macOS App and npm CLI
share the Runtime artifact contract, `~/.one-memory`, receipts, ports,
credentials, Core API, single-writer lock, stable install root, and service
declaration.

### Runtime process shape

The long-running Runtime is a Node process running `runtime.cjs serve`. It owns:

- one Python Core child;
- one recall Broker / Host Gateway sidecar;
- the Runtime, Core, Broker, token, and lock receipts under the data home;
- Core restart supervision while the Runtime process remains alive.

The Electron UI and the npm CLI are control surfaces. They discover the Runtime
through those receipts and verify its loopback identity before attaching. The
Core's data-directory lock prevents a second Core writer, but that lock is not a
replacement for having one unambiguous operating-system startup owner.

### macOS App path

A production App under `/Applications` performs a managed installation before
attaching its UI:

1. Copy the bundled Runtime into
   `~/Library/Application Support/One Memory/{versions,current}`.
2. Install the stable `~/.local/bin/memhub` link.
3. Write an App-owned service receipt under `~/.one-memory`.
4. Install or migrate the single
   `~/Library/LaunchAgents/com.tencent.onememory.runtime.plist` declaration.
5. Wait for the Runtime, Core, and Broker to become verifiably ready.
6. Attach Electron as a client of that Runtime.

The LaunchAgent starts `current/bin/node current/runtime/runtime.cjs serve`
directly. It does not start Electron and does not open a window:

```text
macOS user login
  -> com.tencent.onememory.runtime LaunchAgent
  -> managed Runtime
  -> Python Core + Broker

Electron App -> attach/control only
```

The old `SMAppService.mainAppService` registration is read only for migration:
an App launched by the old login item stays hidden for that launch, then the old
registration is disabled. It is not a second startup owner.

Closing all windows or fully quitting Electron releases the UI's attachment but
does not stop the managed Runtime. The explicit login-start preference controls
a private `runtime-service.enabled` marker used by launchd's
`KeepAlive.PathState`; disabling it affects future login/restart behavior but
does not interrupt the currently running Runtime.

An App-owned service records the exact `/Applications/...app` bundle and
executable paths. The Runtime checks them before starting Core and while
running. If the App is deleted, it removes the enable marker and exits cleanly.
The stable `bin/node` launcher performs the same check before invoking Electron,
covering the case where the Runtime was already stopped when the App was
deleted. With the marker absent, launchd does not restart the job. User Memory
data and the stable Runtime files remain until an explicit uninstall/delete
operation.

### npm Bootstrap / CLI path

The public installation design is a lightweight npm Bootstrap CLI plus an
independently acquired Runtime. The Bootstrap package does not contain the
Python Core, Web UI, Host Bridge, model assets, or a full Runtime. An explicit
`memhub install --yes` resolves and verifies the release, installs the Runtime,
installs CLI links, registers the user service, starts it, and waits for verified
Core readiness in one rollback-capable transaction.

```text
Bootstrap CLI
  -> release manifest and artifact verification
  -> safe extraction to a private staging directory
  -> managed Runtime install transaction
  -> user-level service
  -> Python Core + Host Gateway
```

On macOS, the CLI-managed service uses the same user LaunchAgent at
`~/Library/LaunchAgents/com.tencent.onememory.runtime.plist`. It does not use
`RunAtLoad`; conditional `KeepAlive.PathState` watches the private enable
marker, and an explicit install/start bootstraps the declaration. It launches
`current/bin/node current/runtime/runtime.cjs serve`.
On Linux, it is a systemd user service enabled for `default.target`, with
restart-on-failure and bounded restart backoff. Both are user services: they
start after the user's service/login session exists, not as a pre-login system
daemon.

Install and upgrade preserve managed-install checks, health validation, and
rollback behavior. `npm uninstall` removes only the Bootstrap CLI. `memhub
uninstall` removes the managed service, Runtime and managed links while keeping
Memory data by default. `delete-data` is a distinct confirmed operation.

### Current lifecycle matrix

| Scenario | Startup owner | After user login | Full UI quit | App deletion | Process-level recovery |
| --- | --- | --- | --- | --- | --- |
| macOS App-owned | LaunchAgent | Runtime/Core/Broker start without Electron | Runtime continues | Marker removed and Runtime stops; no restart | launchd restarts Runtime; Runtime restarts Core |
| macOS CLI-owned | Same LaunchAgent | Runtime/Core/Broker start without Electron | No Electron dependency | GUI App deletion has no effect | launchd restarts Runtime; Runtime restarts Core |
| Linux CLI-owned | systemd user service | Runtime starts with the user manager/default target | No UI dependency | Not applicable | systemd restarts Runtime; Runtime restarts Core |

The background Service therefore remains available when the Electron UI is not
running. Stopping Runtime, disabling login start, uninstalling Runtime, and
deleting data remain distinct operations.

### Service ownership

There is exactly one startup owner per data home:

```text
macOS LaunchAgent or approved managed helper
Linux systemd user service
              -> managed Runtime
              -> Core + Broker

Electron App -------- attach/control only
npm CLI ------------- attach/control only
```

The owner receipt is `app` or `cli`. A full `memhub install --yes` or successful
CLI upgrade transaction can take ownership before starting the service and
rolls the owner back on failure. Once CLI-owned, deleting the GUI App does not
stop the service. Merely running `service-install` does not claim the Runtime;
it only repairs/registers the current owner's service.

Release acceptance covers logout/login, complete Mac reboot, notarization,
staple, Gatekeeper, and clean-machine installation. Acceptance evidence is
stored with release artifacts rather than in the source tree.

The Host Bridge exposes only loopback services and host-scoped capabilities; it
does not persist a general-purpose Core bearer token or provide an unrestricted
Core proxy.

## Current Support and Release Boundary

Support is declared per immutable GitHub Release manifest:

- Linux x64 and arm64 Runtime artifacts are built and verified on native GitHub
  Actions runners.
- macOS arm64 and x64 App/Runtime artifacts must carry Developer ID signatures,
  notarization tickets, and native-platform acceptance receipts.
- Windows is not supported.
- `hy-memory==2.0.4` is the public Python SDK dependency.

See [`INSTALLATION.md`](INSTALLATION.md) for the release matrix. A source test,
fake hosting test, or cross-build does not substitute for a native-platform
lifecycle receipt.

## Engineering Rules

- Preserve the source record before model processing.
- Keep source authorization, model egress consent, and host recall scope
  independent.
- Do not treat unavailable source data as deletion.
- Do not create confirmed memory without supporting evidence.
- Keep claims traceable to the narrow original Evidence that supports them.
- Do not let graph layout imply semantic relationships.
- Run the smallest directly relevant tests for ordinary development.
- Run full suites, release gates, packaging, live, soak, or cross-platform
  acceptance only when explicitly requested or authorized for release work.
- Never remove a safety boundary merely to make a test or release gate green.

The current test entry points are documented in
[`../CONTRIBUTING.md`](../CONTRIBUTING.md).
