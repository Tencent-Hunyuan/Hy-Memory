# MemHub Architecture

Updated: 2026-10-21.

Hy Memory 是 SDK / 引擎；MemHub 是产品与发布名。`one-memory` 仍可能出现在
兼容路径、模块名和服务标识中。

---

## Architecture Principles

1. **所有对 memory 的读写操作都交给 SDK**，包括存储 backend；backend 选择与路径一律通过 **SDK config / env** 完成（如 vector / graph / cache / history / coding / raw）。MemHub 不另起一套 memory 存储引擎。
2. **MemHub 内对 memory 的原子操作也一律经 SDK 完成。** SDK 若缺少所需 client 能力，**先补 SDK 接口**，再在产品层调用；禁止在 Core 内直接改 VDB/Graph/SDK 私有库，或再维护一份 memory 正文权威。
3. **MemHub 层不存储 memory 实体与引擎侧关系**（正文、supersede 权威、向量/图副本等）。MemHub 的主责是 **管理面**：harness（来源授权与同步）、LLM model profile、异步写入队列、config 开关、Core/Runtime 状态。  
   - **可以且应当**持久化 SDK 提供的 **`memory_id`（及组织用元数据）**，作为外键引用。  
   - **禁止**再存一份 memory 正文台账、自判 supersede、把 SDK 当可丢缓存。

**直接约束：**

- 原始对话进入 **SDK raw 层**；MemHub 不以 `evidence` 表保存原文权威。
- 列表、纠正、删除和召回通过 **授权 + SDK API** 完成；需要挂接处只存 `memory_id`。
- Matter 尚未形成已发布产品合同；当前全部 `matter*` 占位表和 API 入口删除。

---

## Product and Runtime Shape

MemHub is a local-first memory product.

### Responsibilities

```text
AI hosts / harnesses
  ├─ install / hooks / MCP / Skill / pairing
  ├─ authorized recall (Host Bridge → Core → SDK search)
  └─ future higher-level organization requires a new explicit contract
             │
             ▼
      Python Core / FastAPI   ← 管理面
      ├─ harness grants & sync desired-state
      ├─ model profiles & secrets boundary
      ├─ async write queue (schedule / drain / leases)
      ├─ config switches & Core/Runtime health
      ├─ Hy Memory bridge (only SDK client entry for memory I/O)
      └─ local HTTP API
          ▲        ▲         ▲
          │        │         └─ Host Bridge / recall broker
       Web UI    CLI / Runtime Control
                    ▲
                 Electron

Memory entities / raw / vectors / graph / coding stores
  └─ owned exclusively by Hy Memory SDK (config/env under One Memory Home)
```

There is one managed Runtime and one MemHub data home per installation. CLI and
Electron are clients and lifecycle interfaces to that Runtime; they do not own
separate memory engines. The Web UI is a client of Core and does not access host
files or SDK private stores directly.

## Workspace Ownership

| Path | Responsibility |
| --- | --- |
| `apps/core` | FastAPI 管理面：harness、model、队列、config、Runtime 协作，并经 bridge 调用 SDK。 |
| `apps/web` | React / TypeScript product UI and API client. |
| `apps/desktop` | Electron shell; attaches to or starts the managed Runtime. |
| `apps/cli` | Bootstrap, install, upgrade, service lifecycle, diagnostics. |
| `packages/runtime-control` | Runtime discovery/lifecycle, install transactions, service management. |
| `packages/host-bridge` | Loopback recall broker and host-facing MCP/Skill/Hook/plugin assets. Not a second memory engine. |
| `packages/contracts` | Shared TypeScript API contracts. |
| `packages/operations` | Shared typed operation flows. |
| `scripts` | Dev/privacy/test/packaging gates; not runtime product code. |

Python: `uv` + 3.12. TypeScript: `pnpm` + Node 24. See root `package.json`,
`apps/core/pyproject.toml`, [`../README.md`](../README.md), [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Data and Processing

### Source authorization and synchronization（管理面 · 仍属 MemHub）

Source root authorization and Harness synchronization remain distinct:

- SQLite（或后续等价管理库）`source_grants`：授权根、身份、授予/撤销。Grant alone does not turn sync on.
- Core-owned `<data_dir>/config.json`：per-source `sync_enabled` 等 desired state。
- Core is the only writer of `config.json`（temp + fsync + atomic replace）.
- Web/Desktop/CLI/Runtime call Core APIs; they do not keep a second durable copy.

同步/导入把宿主对话写入 **SDK（含 raw 层）**。队列描述待执行的
SDK 调用，完成态以 SDK 可验证结果和可选 `memory_id` 回执为准。

暂停同步是管理面开关；已进入 SDK 的 memory/raw 生命周期由 SDK API 管理，
不能通过 MemHub 影子表单独改变其可见性。

Periodic polling / fs watch live in Core. UI does not schedule scans.

### Memory processing

#### Write path

```text
authorized harness / user action
  -> (optional) durable job row in MemHub queue  // 调度与租约，不是 memory 实体
  -> sedimentation / model / source gates         // 管理面门禁
  -> HyMemoryBridge
  -> Hy Memory SDK write (raw / chat / coding / …)
  -> SDK stores (vector, graph, cache, history, coding, …) via SDK config/env
  -> MemHub may persist memory_id only for explicit management receipts / UI keys
  -> search / recall / UI via SDK read APIs (+ MemHub authz)
```

- **原子 memory 变更**（create / update / delete / supersede 等）：只通过 SDK。缺接口则扩展 SDK client。
- **异步队列**：只保留 `memory_write_jobs`，其承载 FIFO / event-time、公平调度、租约、暂停、重试与 SDK 回执；一个 job 对应一次 SDK `add()`。旧 `memory_tasks` 与 task receipt 二级台账删除。
- SDK `write_outcomes` 只回答某个 request 是否已由 SDK 提交及返回哪些 `memory_id`；它不是队列，也不接管调度。
- 原文/raw 的持久化与失败保留由 SDK raw 合同承担。
- 运维状态直接来自 `memory_write_jobs`，不另设 `memory_tasks` 二级执行表。

### HY / SDK and MemHub bridge

Hy Memory is the **only** extraction, memory entity, relationship, and semantic-retrieval engine. Product code enters it through `HyMemoryBridge`（共享 Runtime、模型配置、密钥边界），不在业务模块里私自 new 一套绕过门禁的 client。

- Storage backends：仅 SDK config/env（Chroma/FAISS/Qdrant、Kuzu/Neo4j、cache/history sqlite|mysql、coding sqlite 等）。
- Queue：pending `memory_write_jobs` 只表示尚未调用 SDK 的待处理请求，因此没有 SDK raw/memory ID。worker 单次调用 SDK `add()`；SDK 内部完成 raw 写入与 extraction/reconciliation，成功后 MemHub 仅将返回的 raw ID / memory IDs 记作 job outcome。`import_raw()` 只用于历史数据的 exact migration，不拆分正常写路径。
- Recall：Host Bridge / Core 先做宿主安装凭据与 tool grant，再调 **SDK search/get**。当前产品没有逐 memory 的 A-harness→B-harness 可见性开关，默认允许跨 harness 搜索；未来若增加来源限制，直接透传 SDK `source` / `include_sources` / `exclude_sources`，不在 MemHub 建 memory ACL。
- Context wire：cutover marker 提交前只使用 legacy Context v1；提交后 Host Bridge 只使用 SDK-native Context v2。两种 schema 原样校验、原样返回，不把 SDK item 补造成 v1 的 `revision`、`evidence_ids`、`paths` 或 `adoption`，已选择的 v2 路径失败时也不回退 legacy 权威。
- Ask wire：marker 前保留 Governance Ask v1；marker 后 Web / Host Bridge 只调用 SDK Ask v2。v2 的引用只含 SDK 原生 `memory_id/content/layer/status/source/gmt_created`，生成过程中重复 `get` 校验同一 SDK 快照，不补造 revision、Evidence 或 scopes，SDK 失败不回退 v1。
- Matter：未形成已发布产品合同，不迁移占位数据；所有 `matter*` 表在升级清理时物理删除，fresh install 不创建，Runtime 不注册 Matter API。
- 旧用户迁移：由 App 在可见的启动 loading 阶段统一执行；此时 Core 尚未对外提供产品读写。启动流程创建/校验完整 bundle、续跑 ledger、完成 SDK backfill 与 reconciliation，满足全部门禁后才提交 marker 并进入主界面。普通用户不需要另跑 CLI，也不按旧 App 内 SDK 版本分叉迁移。
- Matter 若未来重新开发，需以新的 SDK-native 合同重新设计，不继承当前占位 schema。

Pinned SDK 版本见发布锁文件 / `MEMORY.md`（随发布更新）。

## Permission Boundaries（管理面 · 仍独立）

These permissions answer different questions and must not be collapsed:

1. **Source grant:** may Core read this selected host data root?
2. **Harness sync:** should Core continue discovering/importing from this source into the **SDK write path**?
3. **Model-content permission:** may eligible content be sent to the configured model provider (for SDK extraction etc.)?
4. **Host recall grant:** may this external host invoke the granted recall tools? This is not a per-memory cross-harness ACL.

Host installation / recall authorization cross an external trust boundary; preview, fingerprint, scope, credential, rollback protections remain.

The product must not depend on screen capture, Accessibility/AXTree, OCR,
keyboard/mouse, clipboard, or background audio collection.

## Storage

| 存什么 | 谁拥有 | 怎么配 |
| --- | --- | --- |
| Memory 实体、raw、向量、图、coding、SDK cache/history | **Hy Memory SDK** | SDK config / env；目录落在 One Memory Home 下由 Runtime 约定 |
| harness grants、sync desired-state、model profiles、队列/租约、Core 收据、必要的 `memory_id` 引用 | **MemHub 管理库**（当前多为 `one-memory.sqlite3`） | Core 管理；**不**存 memory 正文 |

MemHub SQLite（或后继）是管理面权威库；HY/SDK stores 是 memory 数据平面
权威。两者职责不同，MemHub 不镜像 memory 实体。

Fresh install 直接 bootstrap 最新管理面 schema，不执行历史 Evidence /
Governance migration 链。旧用户启动迁移可以临时读取旧表，但 cutover、raw
引用回填和 bundle restore 验证全部成功后，必须在 Runtime 对外服务前 DROP
旧 Evidence/Governance/retrieval/session-shadow、全部 Matter 占位表以及迁移专用 alias/ledger。

不要为 MemHub 管理面引入隐式 MySQL 双写。SDK 侧
`MEMORY_CACHE_BACKEND=mysql` 等属于 SDK 合同，与 MemHub 管理库无关。

`config.json`：desired user settings，不是 runtime health 的唯一真相；健康态从收据与进程状态重建。

管理面可持久化 `source_grants`、model profile、队列/租约、call_events
审计元数据（不存对话或记忆正文）和 Runtime 收据。

## Matters / call_events / session inbox（口径）

| 能力 | 当前口径 |
| --- | --- |
| **Matters** | 未发布占位功能，现已删除表与 Runtime API；未来按新合同重新设计。 |
| **call_events** | 本机调用元数据审计（非 memory 实体）；可留在管理面；展示记忆时用 id 调 SDK。 |
| **session inbox / lease / change_outbox** | 已删除的历史管道。未来若需要宿主增量，必须定义管理事件或 SDK 可订阅合同，不得靠 MemHub 影子 memory 表推送正文。 |

## Runtime and Interfaces

The repository has one Runtime implementation, one default data home, and one
OS service identity per installation. macOS App and npm CLI share Runtime
artifact contract, `~/.one-memory`, receipts, ports, credentials, Core API,
single-writer lock, stable install root, and service declaration.

### Runtime process shape

The long-running Runtime is a Node process running `runtime.cjs serve`. It owns:

- one Python Core child;
- one recall Broker / Host Gateway sidecar;
- Runtime/Core/Broker/token/lock receipts under the data home;
- Core restart supervision while Runtime remains alive.

Electron UI and npm CLI discover Runtime via receipts and verify loopback
identity before attaching. Core’s data-directory lock prevents a second Core
writer; it does not replace a single OS startup owner.

### macOS App path

A production App under `/Applications` performs a managed installation before
attaching its UI:

1. Copy the bundled Runtime into
   `~/Library/Application Support/One Memory/{versions,current}`.
2. Install the stable `~/.local/bin/memhub` link.
3. Write an App-owned service receipt under `~/.one-memory`.
4. Install or migrate
   `~/Library/LaunchAgents/com.tencent.onememory.runtime.plist`.
5. Wait for Runtime, Core, and Broker to become verifiably ready.
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

The old `SMAppService.mainAppService` registration is read only for migration.
Closing all windows or quitting Electron does not stop the managed Runtime.
Login-start preference controls `runtime-service.enabled` / launchd
`KeepAlive.PathState`. App deletion removes the enable marker; user Memory data
and stable Runtime files remain until explicit uninstall/delete-data.

### npm Bootstrap / CLI path

Lightweight npm Bootstrap CLI plus independently acquired Runtime. Bootstrap
does not ship Python Core, Web UI, Host Bridge, models, or full Runtime.
`memhub install --yes` resolves/verifies release, installs Runtime, links CLI,
registers user service, starts, waits for Core readiness (rollback-capable).

On macOS, same user LaunchAgent path; CLI-managed service uses
`KeepAlive.PathState` + enable marker. On Linux, systemd user service with
restart-on-failure. `npm uninstall` removes Bootstrap only; `memhub uninstall`
removes service/Runtime/links, keeps data by default; `delete-data` is separate.

### Current lifecycle matrix

| Scenario | Startup owner | After user login | Full UI quit | App deletion | Process-level recovery |
| --- | --- | --- | --- | --- | --- |
| macOS App-owned | LaunchAgent | Runtime/Core/Broker without Electron | Runtime continues | Marker removed; Runtime stops | launchd ↔ Runtime ↔ Core |
| macOS CLI-owned | Same LaunchAgent | Runtime/Core/Broker without Electron | No Electron dependency | GUI deletion no effect | launchd ↔ Runtime ↔ Core |
| Linux CLI-owned | systemd user | Runtime with user manager | No UI dependency | N/A | systemd ↔ Runtime ↔ Core |

### Service ownership

```text
macOS LaunchAgent or approved managed helper
Linux systemd user service
              -> managed Runtime
              -> Core + Broker

Electron App -------- attach/control only
npm CLI ------------- attach/control only
```

Owner receipt is `app` or `cli`. Host Bridge exposes only loopback / host-scoped
capabilities; it does not persist a general-purpose Core bearer as an
unrestricted proxy.

## Current Support and Release Boundary

Support is declared per immutable GitHub Release manifest:

- Linux x64/arm64 Runtime on native GHA runners.
- macOS arm64/x64 App/Runtime: Developer ID, notarization, native acceptance.
- Windows is not supported.
- `hy-memory==2.0.5.dev3` is the current development-preview SDK dependency; the release
  lock and [`../MEMORY.md`](../MEMORY.md) are authoritative when the pin changes.

See [`INSTALLATION.md`](INSTALLATION.md).

## Engineering Rules

- Architecture Principles 不可被「方便测试 / 赶发布」绕过。
- Memory I/O 与 backend 只经 SDK；缺能力先补 SDK。
- MemHub 可存 `memory_id` 引用与管理面状态；不存 memory 实体副本。
- Source grant、model egress、host recall scope 保持独立。
- 不要把「管理库查不到行」当成 SDK 侧已删除，除非已显式调 SDK 删除并确认。
- Do not treat unavailable source data as deletion（宿主文件消失 ≠ 自动删 SDK memory，除非产品显式定义并调 SDK）。
- Do not let graph layout imply semantic relationships.
- Run the smallest relevant tests for ordinary development; full/release/soak only when requested.
- Never remove a safety boundary merely to make a gate green（授权/模型/召回边界）。

Test entry points: [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Upgrade Compatibility

Released installations require the offline, resumable compatibility protocol in
[`contracts/sdk-data-plane-migration-v1.md`](contracts/sdk-data-plane-migration-v1.md).
The App runs it during the startup loading phase before Core exposes product
reads or writes. A cutover requires a complete bundle, resumable backfill,
reconciliation, and verified cleanup; a SQLite-only backup or replaying legacy
rows through extraction is not sufficient.
