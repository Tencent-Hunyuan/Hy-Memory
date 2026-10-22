# MemHub / One Memory Architecture

Updated: 2026-10-21.

**文档约定：** 以文首 **Top Rules（新）** 为现行权威。  
用 ~~删除线~~ 标注的段落是历史设计（曾落地或曾写入本文件），**不再作为实现目标**；保留是为了对照迁移，不是双轨并行。  
Hy Memory = SDK / 引擎。MemHub = 产品与发布名；`one-memory` 仍出现在历史模块、路径与兼容接口名中。

---

## Top Rules（新 · 根本）

1. **所有对 memory 的读写操作都交给 SDK**，包括存储 backend；backend 选择与路径一律通过 **SDK config / env** 完成（如 vector / graph / cache / history / coding / raw）。MemHub 不另起一套 memory 存储引擎。
2. **MemHub 内对 memory 的原子操作也一律经 SDK 完成。** SDK 若缺少所需 client 能力，**先补 SDK 接口**，再在产品层调用；禁止在 Core 内直接改 VDB/Graph/SDK 私有库，或再维护一份 memory 正文权威。
3. **MemHub 层不存储 memory 实体与引擎侧关系**（正文、supersede 权威、向量/图副本等）。MemHub 的主责是 **管理面**：harness（来源授权与同步）、LLM model profile、异步写入队列、config 开关、Core/Runtime 状态。  
   - **可以且应当**持久化 SDK 提供的 **`memory_id`（及组织用元数据）**，作为外键引用。  
   - **禁止**再存一份 memory 正文台账、自判 supersede、把 SDK 当可丢缓存。

**推论（新）：**

- ~~原文 Evidence 业务库~~ → **原文进入 SDK raw 层**；不再以 MemHub `evidence` 表为原文权威。
- ~~Governance / `governed_*` 作为产品级 memory 权威~~ → **拆除方向**；列表/纠正/删除/召回过滤改为 **授权 + 调 SDK**；需要挂接处只存 `memory_id`。
- Matters 等高级组织可以留在 MemHub，但 **改 memory 必须走 SDK**。

---

## Product and Runtime Shape

MemHub is a local-first memory product.

### 新：职责切分

```text
AI hosts / harnesses
  ├─ install / hooks / MCP / Skill / pairing
  ├─ authorized recall (Host Bridge → Core → SDK search)
  └─ (optional) higher-level organization (e.g. Matters) keyed by memory_id
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

### ~~旧：产品叙事（已废弃为权威描述）~~

~~A user authorizes supported AI conversation sources, keeps their original content as local Evidence, processes eligible content through Hy Memory, governs the result, and grants hosts a separate scope for recall.~~

~~```text
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
```~~

**对照：** 管理面（harness、队列、model、config）仍在 Core；~~Evidence + Governance 作 memory/原文权威~~ 按 Top Rules 退出。异步队列仍可调度「调用 SDK 写入」的工作，但 **写入结果的权威在 SDK**，MemHub 至多记录 `memory_id` / 任务回执。

There is one managed Runtime and one MemHub data home per installation. CLI and
Electron are clients and lifecycle interfaces to that Runtime; they do not own
separate memory engines. The Web UI is a client of Core and does not access host
files or SDK private stores directly.

## Workspace Ownership

| Path | Responsibility |
| --- | --- |
| `apps/core` | **新：** FastAPI 管理面：harness、model、队列、config、Runtime 协作、**经 bridge 调用 SDK**。 ~~旧：SQLite 业务库含 Evidence、Governance 记忆台账、HY 集成与 retrieval 权威。~~ |
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

**新：** 同步/导入的目标是把宿主对话 **写入 SDK（含 raw 层）**，而不是写入 MemHub Evidence 权威库。队列可以描述「待调用 SDK 的工作」，完成态以 SDK 可验证结果 + 可选 `memory_id` 回执为准。

~~**旧：** Turning sync on prepares catch-up import into Evidence / `memory_write_jobs`; historical turns without a durable write receipt are added to `memory_write_jobs`. Adapters read host files into MemHub Evidence; they do not call HY directly.~~

~~Turning sync off stops scanning and stops claiming pending jobs; in-flight first HY call may finish; previously published memories remain for recall.~~  
**新补充：** 暂停同步仍是管理面开关；已在 SDK 中的 memory/raw 生命周期由 SDK 与产品调用 SDK 的删除 API 决定，不由 MemHub 影子表「撤销可见性」单独当权威。

Periodic polling / fs watch live in Core. UI does not schedule scans.

### Memory processing

#### 新：唯一写入路径

```text
authorized harness / user action
  -> (optional) durable job row in MemHub queue  // 调度与租约，不是 memory 实体
  -> sedimentation / model / source gates         // 管理面门禁
  -> HyMemoryBridge
  -> Hy Memory SDK write (raw / chat / coding / …)
  -> SDK stores (vector, graph, cache, history, coding, …) via SDK config/env
  -> MemHub may persist memory_id (+ org metadata) for Matters / receipts / UI keys
  -> search / recall / UI via SDK read APIs (+ MemHub authz)
```

- **原子 memory 变更**（create / update / delete / supersede 等）：只通过 SDK。缺接口则扩展 SDK client。
- **异步队列**：`memory_write_jobs` / `memory_tasks`、FIFO / event-time、公平调度、租约、暂停与重试策略继续由 MemHub 持久化和执行；队列 payload 指向「要喂给 SDK 的输入」，不把 MemHub 表当作 memory 实体或沉淀结果权威。
- SDK `write_outcomes` 只回答某个 request 是否已由 SDK 提交及返回哪些 `memory_id`；它不是队列，也不接管调度。

#### ~~旧：Evidence → Governance 路径（已废弃为目标架构）~~

~~```text
authorized source
  -> Evidence
  -> import job / complete conversation turn
  -> memory_write_jobs
  -> persisted event-time window
  -> durable drain
  -> memory_tasks
  -> sedimentation and source gates
  -> Hy Memory bridge
  -> write verification and Governance
  -> search / recall / UI
```~~

~~Evidence is stored before model work so processing failures do not discard source content.~~  
**新：** 原文/raw 的持久化与失败保留策略由 **SDK raw（及 SDK 合同）** 承担；MemHub 不再以 `evidence*` 表为原文权威。

~~`memory_write_jobs` is the sole production intake…~~  
**新：** 若保留同名队列表，其语义降级为 **SDK 调用调度器**，不再隐含「写入 Governance 台账才算产品成功」。

~~`memory_tasks` remains as an internal execution record…~~  
**新：** 任务/租约记录可保留作运维与排障；不得替代 SDK 查询结果作为 memory 真相。

### HY / SDK and MemHub bridge

#### 新

Hy Memory is the **only** extraction, memory entity, relationship, and semantic-retrieval engine. Product code enters it through `HyMemoryBridge`（共享 Runtime、模型配置、密钥边界），不在业务模块里私自 new 一套绕过门禁的 client——门禁指的是 **harness/model/队列**，不是再套一层 memory 正文 Governance。

- Storage backends：仅 SDK config/env（Chroma/FAISS/Qdrant、Kuzu/Neo4j、cache/history sqlite|mysql、coding sqlite 等）。
- Queue：pending `memory_write_jobs` 只表示尚未调用 SDK 的待处理请求，因此没有 SDK raw/memory ID。worker 单次调用 SDK `add()`；SDK 内部完成 raw 写入与 extraction/reconciliation，成功后 MemHub 仅将返回的 raw ID / memory IDs 记作 job/task outcome。`import_raw()` 只用于历史数据的 exact migration，不拆分正常写路径。
- Recall：Host Bridge / Core 先做宿主安装凭据与 tool grant，再调 **SDK search/get**。当前产品没有逐 memory 的 A-harness→B-harness 可见性开关，默认允许跨 harness 搜索；未来若增加来源限制，直接透传 SDK `source` / `include_sources` / `exclude_sources`，不在 MemHub 建 memory ACL。Matters 可用 `memory_id` 集合做组织过滤，但 **内容与生命周期以 SDK 为准**。
- Context wire：cutover marker 提交前只使用 legacy Context v1；提交后 Host Bridge 只使用 SDK-native Context v2。两种 schema 原样校验、原样返回，不把 SDK item 补造成 v1 的 `revision`、`evidence_ids`、`paths` 或 `adoption`，已选择的 v2 路径失败时也不回退 legacy 权威。
- Ask wire：marker 前保留 Governance Ask v1；marker 后 Web / Host Bridge 只调用 SDK Ask v2。v2 的引用只含 SDK 原生 `memory_id/content/layer/status/source/gmt_created`，生成过程中重复 `get` 校验同一 SDK 快照，不补造 revision、Evidence 或 scopes，SDK 失败不回退 v1。
- Matter / UI raw：marker 后 Matter 版本仅保存 SDK memory/raw 引用和完整性 hash，正文与 quote 每次从 SDK 水合；首页、历史搜索、Matter 面板不再读取 legacy memory/Evidence 路由。旧表在离线迁移校验完成后物理删除，审计与 rollback 只依赖 verified bundle。
- 旧用户迁移：由 App 在可见的启动 loading 阶段统一执行；此时 Core 尚未对外提供产品读写。启动流程创建/校验完整 bundle、续跑 ledger、完成 SDK backfill 与 reconciliation，满足全部门禁后才提交 marker 并进入主界面。普通用户不需要另跑 CLI，也不按旧 App 内 SDK 版本分叉迁移。
- Matters 等：MemHub 可存事项结构与 `memory_id[]`；改 memory 内容或生命周期 → SDK。

Pinned SDK 版本见发布锁文件 / `MEMORY.md`（随发布更新）。

#### ~~旧：Governance 层（已废弃为目标架构）~~

~~The bridge and Governance layer preserve Evidence references, authorship, scope, write verification, revisions, corrections, and deletion behavior. A successful SDK response alone is not sufficient evidence of a product-level write. Retrieval results are checked against current Governance and access scope before being returned.~~

~~**旧实现后果（对照用）：** 曾将 SDK 结果镜像进 `governed_*`（含 supersede 状态），并靠解析返回值 / observer / 再 `get` 双写。按 Top Rules，该镜像权威 **拆除**；写后核验若仍需要，应落在 **SDK 合同 + 显式 client API**，而不是第二套实体库。~~

## Permission Boundaries（管理面 · 仍独立）

These permissions answer different questions and must not be collapsed:

1. **Source grant:** may Core read this selected host data root?
2. **Harness sync:** should Core continue discovering/importing from this source into the **SDK write path**?
3. **Model-content permission:** may eligible content be sent to the configured model provider (for SDK extraction etc.)?
4. **Host recall grant:** may this external host invoke the granted recall tools? This is not a per-memory cross-harness ACL.

~~Model-content permission… Tasks do not carry a parallel permission receipt…~~（调度细节可保留实现，不改变 Top Rules。）

Host installation / recall authorization cross an external trust boundary; preview, fingerprint, scope, credential, rollback protections remain.

The product must not depend on screen capture, Accessibility/AXTree, OCR,
keyboard/mouse, clipboard, or background audio collection.

## Storage

### 新

| 存什么 | 谁拥有 | 怎么配 |
| --- | --- | --- |
| Memory 实体、raw、向量、图、coding、SDK cache/history | **Hy Memory SDK** | SDK config / env；目录落在 One Memory Home 下由 Runtime 约定 |
| harness grants、sync desired-state、model profiles、队列/租约、Core 收据、Matters 等组织元数据、`memory_id` 引用 | **MemHub 管理库**（当前多为 `one-memory.sqlite3`） | Core 管理；**不**存 memory 正文 |

~~SQLite is the sole authoritative MemHub business database… It stores Evidence, grants, import jobs, memory tasks, Governance, model profiles…~~  
**新：** MemHub SQLite（或后继）是 **管理面** 权威库，不是 memory 实体权威库。

~~HY-owned vector, graph, cache, and history stores… are not a second authoritative MemHub business database.~~  
**新表述：** HY/SDK stores **就是** memory 数据平面权威；MemHub 不得再镜像一套业务实体与之并行。

Fresh install 直接 bootstrap 最新管理面 schema，不执行历史 Evidence /
Governance migration 链。旧用户启动迁移可以临时读取旧表，但 cutover、Matter/raw
引用回填和 bundle restore 验证全部成功后，必须在 Runtime 对外服务前 DROP
旧 Evidence/Governance/retrieval/Matter-body/session-shadow 表以及迁移专用 alias/ledger。

~~MySQL-only business storage was an earlier proposal… D56 selected SQLite…~~  
**仍有效的工程约束：** 不要静默上 MemHub 业务双写 MySQL；SDK 侧 `MEMORY_CACHE_BACKEND=mysql` 等属 **SDK 合同**，与 MemHub 管理库无关。

`config.json`：desired user settings，不是 runtime health 的唯一真相；健康态从收据与进程状态重建。

### ~~旧表族（拆除方向 · 对照）~~

~~- `evidence*` / import evidence 权威 → 改 SDK raw~~  
~~- `governed_memories*` / supersessions / versions 作列表与生命周期权威 → 改 SDK API + 可选 `memory_id` 引用~~  
~~- 以 Governance 为准的 visibility 正文缓存若依赖实体台账 → 随台账拆除或改为纯派生且可重建，且不得成为写权威~~

**可保留方向（管理面）：** `source_grants`、model profile 相关表、队列/任务租约表、Matters 组织表（含 `memory_id`）、call_events 类审计元数据（不存对话/记忆正文）、Runtime 收据。

## Matters / call_events / session inbox（口径）

| 能力 | 新口径 |
| --- | --- |
| **Matters** | SDK memory 之上的高级组织；MemHub 可管事项结构并持久化 `memory_id`；**改 memory 必须走 SDK**。 |
| **call_events** | 本机调用元数据审计（非 memory 实体）；可留在管理面；展示记忆时用 id 调 SDK。 |
| **session inbox / lease / change_outbox** | ~~旧：围绕 Evidence/变更外发的宿主增量管道~~。Evidence 权威拆除后，若仍需要宿主增量，应重定义为 **管理事件或 SDK 可订阅合同**，不得靠 MemHub 影子 memory 表推送正文。 |

## Runtime and Interfaces

（以下 Runtime / 安装 / 服务归属 **仍现行**，与 Top Rules 不冲突；未改语义处保持原文。）

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

### 新（优先）

- Top Rules 1–3 不可被「方便测试 / 赶发布」绕过。
- Memory I/O 与 backend 只经 SDK；缺能力先补 SDK。
- MemHub 可存 `memory_id` 引用与管理面状态；不存 memory 实体副本。
- Source grant、model egress、host recall scope 保持独立。
- 不要把「管理库查不到行」当成 SDK 侧已删除，除非已显式调 SDK 删除并确认。

### ~~旧工程句（与 Top Rules 冲突者作废）~~

~~- Preserve the source record before model processing.~~（改为 SDK raw 合同）  
~~- Do not create confirmed memory without supporting evidence.~~（证据/raw 关系由 SDK 合同表达）  
~~- Keep claims traceable to the narrow original Evidence that supports them.~~（改为可追溯到 SDK raw / 引用 id）  

仍有效：

- Do not treat unavailable source data as deletion（宿主文件消失 ≠ 自动删 SDK memory，除非产品显式定义并调 SDK）。
- Do not let graph layout imply semantic relationships.
- Run the smallest relevant tests for ordinary development; full/release/soak only when requested.
- Never remove a safety boundary merely to make a gate green（授权/模型/召回边界）。

Test entry points: [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Migration note（给实现与评审）

本文件更新 **先改合同与目标架构**，不表示代码已拆完。实现迁移顺序建议：

1. 冻结新功能写入 `governed_*` / `evidence*` 权威路径。  
2. 记忆库 UI / recall 改为 SDK API + 授权；组织层只挂 `memory_id`。  
3. 导入/同步改为 SDK raw/write；退役 Evidence 权威。  
4. 删除或清空镜像台账；队列改为纯调度。  
5. 缺的 SDK API 开 SDK PR，而不是在 Core 里补影子表。

Released installations require the offline, resumable compatibility protocol in
[`contracts/sdk-data-plane-migration-v1.md`](contracts/sdk-data-plane-migration-v1.md).
Do not cut over an existing home with a SQLite-only backup or by replaying
Evidence through extraction.
