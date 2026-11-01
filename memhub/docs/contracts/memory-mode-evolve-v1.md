# Memory mode, evolve and the nightly schedule v1

Status: Core + CLI implemented (PR B of the SDK-surface batch). The App
controls are built separately against this contract.

All routes need the Runtime bearer token. Unknown query parameters and body
fields answer `422 {"detail": "request_validation_failed"}`, like every
memory route. `user_id` is optional (default `local`) and must match
`^[A-Za-z0-9._-]{1,64}$`. The App never sends it.

## 1. What the modes are

| Mode | SDK client | What happens to a conversation | What search reads |
|---|---|---|---|
| `lite` | `mode="lite"` | Stored as one raw turn with one embedding. No model call; no generation model needs to be configured. | Raw turns only (the SDK forces `L1_RAW`). |
| `pro` (default) | `mode="pro"` | Stored as raw, then a model extracts memories (System 1). The raw becomes SHADOW once extraction succeeds. | Extracted memories (facts, profile, intentions). |
| `ultra` | `mode="ultra"` | Same as pro, and new facts are also projected into the knowledge graph. Every night, evolve runs System 2 for every user. | Same as pro, plus the graph-backed lanes below. |

The product difference between pro and ultra is the nightly evolve. MemHub ultra
uses SDK `mode="ultra"` because `client.evolve` refuses any other mode. Compared
with pro, SDK ultra (hy-memory 2.0.9) also turns on the following, so the owner
can judge their cost:

- **The graph store.** Kuzu is always opened in ultra. Client init fails if the
  graph cannot open, so `PUT ultra` answers `409 graph_store_unavailable` when
  `kuzu` is missing. In pro it opens only when `MEMORY_ENABLE_GRAPH` is set, and
  MemHub does not set it. Writes project new L2 facts into the graph.
- **The schema lane (L6_SCHEMA).** The search's schema channel runs only when
  a graph store exists, so in MemHub only in ultra. It returns the schemas that
  evolve wrote (`schema_limit`, `schema_min_score`).
- **Graph-aware ranking on the normal lane.** With a graph store, search adds
  graph-entity (MENTIONS) boosts when the query names known entities and no
  source filter is set. The heavier graph stages need the graph *and* their
  own env flag, and every flag defaults off, so none of them run in MemHub:
  graph rerank and episode expansion (`MEMORY_GRAPH_RERANK_ENABLED`), topic
  graph entry, same-series bundle, hierarchy bundle.
- **The profile.** `get_user_profile` appends the balanced catalog and the
  specific supplement from the graph, when the SDK's balanced-catalog flag is
  on.

`ONE_MEMORY_GRAPH_STORE` (development only) still forces ultra whatever the
setting says. Any value other than empty or `none` does this. The API reports
it as `forced_by_env: true`, with `configured_mode` showing the saved choice.

**Raw is never deleted.** In every mode MemHub keeps every raw turn the SDK
stored, and the SDK never deletes raw itself. There is no retention window.

## 2. Switching mode: what changes and what does not

The SDK migrates nothing when the mode changes. MemHub applies these rules,
and they are the owner's:

- The mode is read **when a queued job runs**, not when it was queued.
  - A job that already completed, in any mode, stays completed. It is never
    reclaimed or reprocessed.
  - Jobs still waiting (pending, assembling, waiting) and new input run in
    the new mode.
- **A batch in flight finishes in the mode it started in.** Its SDK client is
  retired only when its last use ends (the #218 use count). The next claim
  uses the new mode.
- **Search follows the new mode at once.** The read client is rebuilt on the
  next request (the active-profile read handle is keyed by profile and mode); the client it replaces closes once no request still uses it.
- **lite → pro/ultra:**
  - what lite stored stays raw and is not extracted;
  - lite's raw-only search stops at once;
  - nothing is re-ingested or backfilled.
- **pro/ultra → lite:** memories made by the higher mode stay on disk, but lite
  search does not serve them. Nothing is deleted.
- **ultra → pro/lite:** the nightly evolve stops.
- **→ ultra:**
  - every known user's existing facts are added to the graph once
    (`build_memory_graph(user_id, limit=10000, clear_first=False)`);
  - this runs in the background, one user at a time, never beside that
    user's write;
  - progress is reported in `graph_build`.
- Vector collection names never depend on the mode. The mode is a separate
  client parameter, not part of the embedding-space identity.

## 3. `GET /api/v1/settings/memory-mode`

No parameters. `200` response:

```json
{
  "mode": "pro",
  "configured_mode": "pro",
  "forced_by_env": false,
  "modes": ["lite", "pro", "ultra"],
  "graph_build": {
    "state": "idle",
    "started_at": null,
    "finished_at": null,
    "users": {}
  }
}
```

- `mode` is what SDK clients are built with now; `configured_mode` is the
  saved setting.
- `graph_build.state` is `idle | running | succeeded | failed`. Each entry in
  `graph_build.users[<user_id>]` is
  `{state: pending|running|succeeded|failed, memories_scanned, nodes, error_code}`.

## 4. `PUT /api/v1/settings/memory-mode`

Body: `{"mode": "lite" | "pro" | "ultra"}`. `200` response: the GET shape,
plus these fields:

```json
{
  "previous_mode": "pro",
  "effects": ["existing_memories_added_to_graph"]
}
```

`effects` is empty when the effective mode did not change. That happens when
the mode was already set, or when `ONE_MEMORY_GRAPH_STORE` forces ultra; the
saved setting still changes in that case. Otherwise `effects` lists these, in
this order:

| Effect | When | Meaning |
|---|---|---|
| `earlier_raw_not_extracted` | from lite | What lite stored stays raw; it is not extracted. |
| `lite_search_stops` | from lite | lite's raw-only search ends now; search reads memories. |
| `higher_mode_memories_hidden` | to a lower mode | Memories of the higher mode stay stored but are not shown. |
| `evolve_stopped` | from ultra | The nightly evolve stops. |
| `existing_memories_added_to_graph` | to ultra | The one-time graph backfill started (see `graph_build`). |
| `graph_backfill_already_running` | to ultra | A backfill from an earlier switch is still running; no second one started. |

Errors:

- `409 {"detail": "graph_store_unavailable"}`: `ultra` was asked for, but the
  graph store package is not installed. Nothing changed.
- `422 request_validation_failed`: an unknown mode or an extra field.

Show the planned effects before calling PUT. Clients can compute them with the
table above: the CLI (`memhub settings mode <m>` without `--yes`) and Core
(`memory_mode_effects`) use the same rule.

## 5. Evolve

### `POST /api/v2/evolve` → `202`

Body (every field optional):

```json
{"user_id": "local", "agent_id": "all-apps", "max_batches": 3}
```

- `agent_id` is 1..128 characters. The default is `all-apps`, the agent
  MemHub writes every memory under.
- `max_batches` is 1..1000. The default is the SDK's own.

The response is the run receipt below, with `state: "running"`. The run goes
on in the background.

- **One run per user.** Asking while that user's run is going answers the same
  receipt, again `202`.
- **Never beside that user's writes.** The run waits for the user's write in
  flight. While it runs, that user's queued jobs wait: they are returned to
  pending before any SDK call. Other users are not held.
- **Loops while the SDK answers `should_continue: true`**, up to 20 rounds. A
  run stopped by that cap has `capped: true`; start it again to continue.

Errors:

- `409 {"detail": "memory_mode_not_ultra"}`: the effective mode is not ultra.
- `422`: a malformed `user_id`, `max_batches` outside its range, or an extra
  field.

### Run receipt

```json
{
  "run_id": "uuid",
  "user_id": "local",
  "agent_id": "all-apps",
  "max_batches": null,
  "trigger": "manual",
  "state": "succeeded",
  "started_at": "2026-11-01T03:00:00Z",
  "finished_at": "2026-11-01T03:00:42Z",
  "rounds": 2,
  "tasks_processed": 5,
  "batch_failures": 0,
  "cycle_status": "complete",
  "should_continue": false,
  "continuation_reason": null,
  "remaining_clusters": 0,
  "remaining_actionable_clusters": 0,
  "remaining_fresh_facts": 0,
  "capped": false,
  "elapsed_ms": 41873.2,
  "error_code": null,
  "failure_kind": null,
  "request_id": "sdk-request-id",
  "results": {},
  "cross_domain_sweeper": {}
}
```

- `trigger` is `manual` or `scheduled`.
- `state` is `running`, `succeeded` or `failed`.
- `cycle_status` is the SDK's `partial` or `complete`.
- `error_code` and `failure_kind` are stable codes on failure. Examples:
  `memory_evolve_cancelled` (the Runtime stopped mid-run),
  `hy_memory_evolve_rejected` (the SDK answered `success: false` without a
  code), or the SDK's own `error_code`. Provider messages never appear.
- `results` and `cross_domain_sweeper` are the last round's SDK bodies. They
  live in memory only and are never written to disk or diagnostics.

### `GET /api/v2/evolve/{run_id}?user_id=` → receipt

Returns `404 {"detail": "evolve_run_not_found"}` in two cases:

- the run belongs to another user (answered exactly like a missing run);
- the Runtime restarted since the run. Receipts live in memory, and the last
  64 are kept.

### `GET /api/v2/evolve?user_id=` → overview

```json
{
  "mode": "ultra",
  "schedule": {"enabled": true, "time": "03:00"},
  "users": {
    "local": {
      "last_run_at": "2026-11-01T03:00:00Z",
      "last_result": {"state": "succeeded", "rounds": 2, "...": "receipt without results"},
      "last_error": null
    }
  },
  "running": []
}
```

- Without `user_id` the overview covers every user; with it, that one user.
- `users` survives restarts. It is kept in `<data_dir>/evolve-state.json`,
  counts and codes only.

## 6. Nightly schedule

### `GET /api/v1/settings/evolve-schedule`

Returns `{"enabled": true, "time": "03:00"}`. `time` is `HH:MM`, 24-hour, in
the machine's local time.

### `PUT /api/v1/settings/evolve-schedule`

Body: `{"enabled"?: bool, "time"?: "HH:MM"}`. An absent field keeps its
value. The response is the saved schedule. A bad time answers `422`.

How the schedule runs:

- It runs only in ultra.
- It checks every 5 minutes. A user's run is due when either holds:
  - today's slot has passed, and the user's last run is older than that slot;
  - the user's last run is more than 24 hours old.

  The second rule is how a machine that slept through 03:00 catches up when
  it wakes.
- A user who never ran is measured from the first time the schedule ran in
  ultra, not from the epoch. So switching to ultra at noon does not evolve
  everyone at once; their first run is at the next slot.
- Users run one after another.

The schedule runs every user that has memories. The SDK cannot list users, so
Core takes them from its own records:

- `local`;
- every user holding a source grant (a Hermes profile);
- every user whose turns or notes passed the write queue.

A user is kept when `memory_stats` reports any memory or fact for them.

## 7. Status fields (R10)

`GET /api/v1/memory-processing` has a new optional field, `pipeline`:

```json
{
  "pipeline": {
    "mode": "ultra",
    "configured_mode": "ultra",
    "forced_by_env": false,
    "evolve": {"...": "the overview of section 5"},
    "last_evolve_at": "2026-11-01T03:00:00Z",
    "graph_build": {"...": "as in section 3"}
  }
}
```

`memhub status --json` adds a top-level projection of it:

```json
{
  "pipeline": {
    "mode": "ultra",
    "configured_mode": "ultra",
    "forced_by_env": false,
    "last_evolve_at": "...",
    "evolve_schedule": {"enabled": true, "time": "03:00"},
    "evolve_running": 0,
    "evolve_users": {
      "local": {"last_run_at": "...", "last_error": null, "state": "succeeded"}
    },
    "graph_build": "succeeded"
  }
}
```

`memhub progress --json` carries the same `pipeline`. Both are `null` against
an older Core.

## 8. CLI

```bash
memhub settings mode                    # show the current mode
memhub settings mode lite               # print the plan, change nothing
memhub settings mode lite --yes         # switch
memhub evolve [--user-id <id>] [--agent-id <id>] [--max-batches <n>] [--wait] [--json]
```

## 9. Copy the UI can reuse

| Key | zh-CN | en | es |
|---|---|---|---|
| title | 记忆模式 | Memory mode | Modo de memoria |
| lite | lite：不调用模型，保留全部原始对话，只在原始对话里检索。 | lite: no model calls. Every original conversation is kept, and search reads only those conversations. | lite: sin llamadas al modelo. Se conservan todas las conversaciones originales y la búsqueda solo las consulta a ellas. |
| pro | pro：由模型从对话中提取记忆（System 1）。 | pro: a model extracts memories from your conversations (System 1). | pro: un modelo extrae memorias de tus conversaciones (System 1). |
| ultra | ultra：在 pro 之上每晚自动整合记忆（System 2，evolve），并使用知识图谱。 | ultra: pro plus nightly memory consolidation (System 2, evolve), using a knowledge graph. | ultra: pro más una consolidación nocturna de las memorias (System 2, evolve), con un grafo de conocimiento. |
| confirm | 切换到 {mode} 前请确认： | Before switching to {mode}, please confirm: | Antes de cambiar a {mode}, confirma lo siguiente: |
| earlier_raw_not_extracted | lite 下已处理完的对话不会再被提取；只有尚未处理的和之后的新对话会由模型整理成记忆。 | Conversations already handled in lite are not extracted again; only conversations still waiting, and new ones, become memories. | Las conversaciones ya procesadas en lite no se vuelven a extraer; solo las pendientes y las nuevas se convierten en memorias. |
| lite_search_stops | 只在原始对话里的检索会立即停止，改为检索记忆。 | Search over original conversations stops now; search reads memories instead. | La búsqueda en las conversaciones originales se detiene ahora; se buscará en las memorias. |
| higher_mode_memories_hidden | 更高模式生成的记忆仍然保存，但在这个模式下不会显示。 | Memories created by a higher mode stay stored but are not shown in this mode. | Las memorias creadas por un modo superior se conservan, pero no se muestran en este modo. |
| evolve_stopped | 每晚的自动整合（System 2）会停止。 | Nightly consolidation (System 2) stops. | La consolidación nocturna (System 2) se detiene. |
| existing_memories_added_to_graph | 已有记忆会在后台加入知识图谱。 | Your existing memories are added to the knowledge graph in the background. | Tus memorias actuales se añaden al grafo de conocimiento en segundo plano. |
| graph_backfill_already_running | 知识图谱正在补建中。 | The knowledge graph is still being built. | El grafo de conocimiento todavía se está construyendo. |
| raw_kept | 原始对话全部保留，任何模式都不删除。 | Every original conversation is kept, in every mode. | Se conservan todas las conversaciones originales, en cualquier modo. |
| graph_build_running | 正在把已有记忆加入知识图谱… | Adding existing memories to the knowledge graph… | Añadiendo las memorias actuales al grafo de conocimiento… |
| forced_by_env | 开发环境变量 ONE_MEMORY_GRAPH_STORE 正在强制使用 ultra。 | The development variable ONE_MEMORY_GRAPH_STORE is forcing ultra. | La variable de desarrollo ONE_MEMORY_GRAPH_STORE está forzando ultra. |
| graph_store_unavailable | 这个安装缺少知识图谱组件，无法切换到 ultra。 | This installation lacks the knowledge-graph component, so it cannot switch to ultra. | A esta instalación le falta el componente del grafo de conocimiento; no puede cambiar a ultra. |
| schedule | 每天 {time} 自动整合 | Consolidates daily at {time} | Consolida cada día a las {time} |
| last_evolve | 上次整合：{time} | Last consolidation: {time} | Última consolidación: {time} |
| never_evolved | 尚未整合 | Not consolidated yet | Aún no consolidado |
| evolve_not_ultra | 记忆整合只在 ultra 模式下可用。 | Consolidation is available in ultra mode only. | La consolidación solo está disponible en el modo ultra. |
