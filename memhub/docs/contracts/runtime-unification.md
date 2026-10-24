# MemHub Runtime Unification Contract

Updated: 2026-10-22.

MemHub has one Runtime implementation, one data home, and one service identity
per installation. The macOS App, CLI, and Web UI are clients of that Runtime;
they do not own separate memory engines or databases.

Platform support and release artifacts are defined by
[`../INSTALLATION.md`](../INSTALLATION.md).

## Components

### Runtime

The long-running Node Runtime owns:

- one Python Core child;
- one loopback Recall Broker / Host Gateway sidecar;
- Runtime, Core, Broker, token, and lock receipts under the data home;
- Core restart supervision while Runtime remains alive;
- the packaged Web UI and local control surface.

### Core

Python/FastAPI Core is the MemHub management plane. It owns source grants,
model profiles, the durable SDK-call queue, configuration, diagnostics, and
Runtime coordination.

Memory entities, raw conversations, vectors, graph data, coding memory, and SDK
cache/history belong to the Hy Memory SDK data plane. Core obtains a public
`HyMemoryClient` through the shared runtime lease; no product module may write
SDK private stores or maintain a second memory authority.

### Desktop and CLI

Electron provides native windows, directory selection, display preferences,
and managed installation. It discovers and attaches to the Runtime.

The `memhub` CLI installs, starts, stops, upgrades, diagnoses, and removes the
same Runtime. `one-memory` remains a compatibility command where required by
existing installations.

Closing the UI does not stop a service-managed Runtime.

## Data home

The default data home is `~/.one-memory`, overridable with
`ONE_MEMORY_DATA_DIR`.

```text
One Memory Home/
  one-memory.sqlite3   # MemHub management database
  config.json          # desired user settings
  hy-memory/           # SDK-owned data-plane stores
  backups/             # verified migration bundles
  runtime.json         # non-secret discovery receipt
  runtime-token        # owner-only local credential
  host-permissions/    # host installation identity and grants
  integrations/        # Broker discovery
  logs/
```

MemHub SQLite stores management state and necessary SDK IDs, not memory bodies
or raw conversation authority. SDK backend selection is controlled only by SDK
configuration and environment variables.

## Runtime discovery and authentication

Discovery receipts contain no secrets and identify the service, instance, PID,
loopback host, port, and start time. A client may attach only when:

- receipt schema and service identity are recognized;
- the host is exact loopback and the port is valid;
- the owning process is alive;
- the owner-only token is readable;
- health reports the same service and instance ID.

If a live process cannot be verified, the client fails closed. It must not kill
the process, claim the data home, overwrite receipts, or fall back to anonymous
access.

Browser access uses a constrained loopback session. It does not expose a
general-purpose Core bearer token.

## Service ownership

One service owner is recorded as `app` or `cli`.

- macOS uses one user LaunchAgent for both App- and CLI-managed installs.
- Linux uses a systemd user service where supported.
- Windows is not supported until its service lifecycle and SID/DACL security
  are implemented and validated on native hardware.

App deletion removes only an App-owned enable marker. CLI-owned services remain
under CLI control. Runtime uninstall and data deletion are separate,
explicitly confirmed operations.

## Installation and upgrade

All installers consume the same immutable Runtime artifact and release
manifest for a platform, architecture, version, and commit. Installation:

1. validates the manifest and every file hash;
2. stages a complete version directory;
3. atomically switches the managed `current` pointer;
4. installs the managed CLI link and user service;
5. starts the Runtime and verifies Runtime/Core/Broker identity;
6. rolls back files and pointers created by the failed transaction.

Upgrade installs a new immutable version and switches only after validation.
Health-check failure restores the previous version. Binary rollback is not data
rollback; post-cutover data compatibility follows
[`sdk-data-plane-migration-v1.md`](sdk-data-plane-migration-v1.md).

## Single-writer and lifecycle rules

- One data home has at most one Runtime and one Core writer.
- Runtime owns Core/Broker startup and shutdown ordering.
- Electron and CLI attach only after identity validation.
- A client stops only a Runtime it is authorized to control.
- Uninstall removes managed services, Runtime versions, and links but preserves
  user data by default.
- `memhub delete-data` requires the Runtime to be stopped and exact-path
  confirmation.

## Acceptance

A supported release verifies:

- App and CLI attach to the same data home and Runtime;
- duplicate Runtime/Core startup is rejected;
- receipts, tokens, and loopback identity fail closed when invalid;
- Runtime survives UI exit and restarts Core when appropriate;
- install and upgrade rollback preserve the prior working version;
- uninstall preserves data and delete-data requires separate confirmation;
- artifacts and service behavior pass native-platform acceptance.
