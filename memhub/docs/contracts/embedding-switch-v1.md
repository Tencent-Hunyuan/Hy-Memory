# Embedding Switch v1

Updated: 2026-11-01.

Status: Core, CLI and App implemented. The App presents this as a user-facing
"memory search" and "index update" flow; protocol names remain unchanged.

This contract is what the App and the CLI build on to change the embedding
model without losing a memory, and to fold in vector data an earlier build left
in another embedding space. Core owns every decision; clients plan, confirm,
start, follow and cancel. Core implementation: `embedding_switch.py`,
`embedding_switch_routes.py`, `embedding_spaces.py`.

## 1. Rules

1. **Identity is model name + dimension.** Two profiles with the same
   embedding model and the same width share an embedding space, whatever the
   provider or endpoint. Changing only the endpoint, provider, key or anything
   about generation never re-embeds.
2. **Activation never changes the space by itself.** A profile whose embedding
   identity differs from the live space's needs a *switch*:
   `POST /api/v1/model-profiles/{id}/activate` answers
   `409 embedding_switch_required` with the plan. Exception: a home with no
   vector at all (no collection holds a record, no graph, no write in flight)
   activates directly; nothing has to be carried over.
3. **A switch re-embeds every vector of every user** (all layers, raw included,
   coding keys, working facts, entities; and in ultra mode the graph) from the
   stored text into a new space, verifies it, then commits in one SQLite
   transaction: the new space live, the old one retired, the target profile
   active. Nothing is renamed at commit.
4. **Reads keep serving the old space until the commit.** Recall, library,
   search and context results do not change while a switch runs.
5. **Model writes pause; intake keeps queueing.** From the moment a job exists
   until it is `done` or `rolled_back`, no conversation is turned into memory
   and direct edits/deletes answer `409 embedding_switch_in_progress`. Imports,
   live sync, manual adds and `/api/v2/memory-writes` still queue; the queue
   sediments into the new space after the commit.
6. **Failure or cancel keeps the old model.** The source space is never
   written. A failed or cancelled job drops what it built and ends
   `rolled_back`; the old profile stays active.
7. **Startup scan and merge.** On every Core start, before model writes resume,
   Core scans the vector store. A non-empty space that is neither live nor
   known to the registry (*stray*: an earlier build wrote one collection and
   read another) is merged: its ids missing from the live space are
   re-embedded into the live space with the live embedder. The live space's
   records win. A merged stray is retired. A stray with nothing missing is
   retired at once, without a job. The same fold-in runs inside every switch.
8. **Retired spaces are kept 14 days**, then dropped by a daily sweep, or
   earlier with `DELETE /api/v1/embedding-spaces/{id}`. A retired space is
   never folded back in: its records are already live, and folding it in again
   would bring back memories deleted since.
9. **Restarts resume.** A job is persisted with a heartbeat. Quitting the App,
   a crash, a reboot or an App update installing a new build mid-switch: the
   next serving Core resumes the job from its state. The upgrade migration
   (`sdk-migrate`) never touches the switch tables; `sdk-migration-status`
   reports an open job as `embedding_switch` and does not block on it.
10. **Graph.** Any graph the live space holds is rebuilt at the target width,
    whatever the mode, so a pro home keeps the graph of its ultra days. In ultra
    every SDK client is closed while the graph is copied and again while it is
    verified, so reads wait for those two steps; the other modes keep reading.

## 2. Space registry

`GET /api/v1/embedding-spaces` lists `spaces[]` (registry rows) and
`scanned[]` (what the vector store holds).

| Field | Meaning |
|---|---|
| `space_id` | uuid |
| `state` | `live` · `building` (a switch target) · `stray` (found by the scan, waiting to be merged) · `retired` · `dropped` |
| `model`, `dims` | embedding identity; `model` is null for a stray, `dims` null until measured |
| `name_dims` | the width the collection names carry; null for an "auto" space (old name kept) |
| `collection_base` | physical base; existing homes keep their pre-registry name byte for byte |
| `graph_dir` | graph directory of the space (`graph` for the first) |
| `vectors` | count at the last scan (display only) |
| `live_since`, `retired_at`, `keep_until`, `dropped_at` | ISO 8601 timestamps; `keep_until = retired_at + 14 days` |

`scanned[]` entries: `collection_base`, `name_dims`, `width`, `vectors`,
`counts` (per role), `space_id` and `state` (`unregistered` when no row names
it). `scanned` is null with `scan_error` set when the scan could not run (for
example `sdk_reembed_unavailable` on an SDK older than 2.0.10).

## 3. Endpoints

All routes need the local API token (or a valid UI session). Errors keep
Core's shape `{"detail": "<code>"}`; a refusal that computed a plan adds
`"plan": {...}`.

| Method and path | Request | Success | Purpose |
|---|---|---|---|
| `POST /api/v1/embedding-switches/plan` | `{profile_id, expected_revision?}` | 200 plan | No side effects. |
| `POST /api/v1/embedding-switches` | `{profile_id, expected_revision, plan_token, requested_by: "app"\|"cli"}` | 202 job | Start a switch. A stale `plan_token` is refused. |
| `GET /api/v1/embedding-switches/current` | — | 200 current | Poll while a dialog, banner or loading screen shows it (1 s). `Cache-Control: no-store`. |
| `GET /api/v1/embedding-switches/{job_id}` | — | 200 job | One job. |
| `POST /api/v1/embedding-switches/{job_id}/cancel` | — | 200 job | Sets `cancel_requested`; honoured at the next batch. |
| `GET /api/v1/embedding-spaces` | — | 200 | Registry + scan. |
| `DELETE /api/v1/embedding-spaces/{space_id}` | — | 200 `{space}` | Drop a **retired** space now. |
| `POST /api/v1/model-profiles/{id}/activate` | — | 200 profile | **Changed**: see §1 rule 2. |
| `GET /api/v1/memory-processing`, `GET /api/v1/health` | — | — | New field `embedding_switch` (§4.1). |

### 3.1 Plan

```json
{
  "needed": true,
  "reason": "embedding_identity_changed | same_embedding | empty_home",
  "plan_token": "…",
  "source": {"space_id": "…", "model": "bge-small-zh-v1.5", "dims": 512, "collection_base": "memhub_…", "vectors": 12480},
  "target": {"space_id": null, "model": "text-embedding-v3", "dims": 1024, "collection_base": ""},
  "target_profile_id": "…", "target_revision": 5,
  "target_provider_kind": "openai_compatible", "target_local": false,
  "sources": [{"space_id": "…", "collection_base": "…", "dims": 512, "vectors": 12480, "unmerged": null}],
  "total_vectors": 12480, "users": 2, "graph_vectors": null,
  "estimate_s": 63, "requests": 195,
  "disk_bytes_needed": 73400320, "disk_bytes_free": 101234567890,
  "retention_days": 14,
  "blockers": []
}
```

- `needed: false` with `same_embedding`: activate as before (generation-only
  change, or the same model and width at another endpoint).
- `needed: false` with `empty_home`: activate; Core moves the space in the same
  transaction.
- `sources[0]` is the live space; further entries are strays folded in
  (`unmerged` = their record count). `users` counts distinct `user_id`s.
- `requests` is null for a local target (no cost). `estimate_s` is a rough
  guide (local ≈ 60 vectors/s, remote ≈ 200 vectors/s), not measured.
- `graph_vectors` is null when there is no graph, and also when an SDK client
  holds it open, as in ultra: the estimate and disk figures then leave the graph
  out. The switch still rebuilds it.
- A non-empty `blockers` means the switch cannot start (§5.1).

### 3.2 Job

```json
{
  "job_id": "…", "kind": "switch | merge",
  "state": "preparing | reembedding | verifying | committing | done | failed | rolled_back",
  "source": {…space summary…}, "target": {…space summary…},
  "stray_space_ids": [], "target_profile_id": "…", "target_revision": 5,
  "requested_by": "app | cli | startup",
  "progress": {
    "phase": "prepare | vectors | graph | verify | commit | rollback | done",
    "done": 6240, "total": 12480, "percent": 50.0,
    "embedded": 6240, "reused": 0, "kept": 0,
    "role": "memory", "source_index": 1, "sources": 1,
    "rate_per_s": 180.2, "eta_s": 35, "updated_at": "…"
  },
  "cancel_requested": false, "error_code": null, "attempt": 1,
  "created_at": "…", "updated_at": "…", "finished_at": null
}
```

| State | Meaning | Writes |
|---|---|---|
| `preparing` | Gate closed; waiting (≤ 10 min) for model writes already past their claim | paused |
| `reembedding` | Vectors (then graph) re-embedded; `progress` advances | paused |
| `verifying` | Ids, documents, metadata, widths and per-user counts compared | paused |
| `committing` | The single SQLite transaction | paused |
| `done` | Terminal. New space live (switch) or strays retired (merge) | resume |
| `failed` | **Rollback in progress** (not terminal): dropping the partial target | paused |
| `rolled_back` | Terminal. Old space and profile unchanged; `error_code` says why | resume |

`kind: "merge"` jobs come from the startup scan (`requested_by: "startup"`);
their target is the live space. Cancel is accepted in `preparing`,
`reembedding` and `verifying`.

### 3.3 Current

```json
{
  "schema_version": 1,
  "phase": "idle | scanning | running",
  "writes_paused": true,
  "scan_state": "pending | scanning | done | failed | unavailable",
  "scan_error": null,
  "job": {…job…} ,
  "last_finished": {…job…}
}
```

`job` is the open job or null; `last_finished` the most recent terminal job,
so a client can show the outcome after it ends.

### 3.4 Probe

The probe result shape is unchanged. What changed is what the probe records:

- On the profile that fills the live space (the active one, or the one the
  space was built with), a measured width that differs from the space's width
  is **never** recorded. Recording it used to move writes to new, empty
  collections silently (B2).
- A measured width equal to the width the space already stores is a
  *relabel*: it is recorded, and no collection name changes. An "auto" space
  whose width was never recorded takes the measured width when Chroma stores
  the same width, or when it stores nothing yet.
- Every other profile records what it measured, as before.

A probe never tells the App that a switch is needed. The App learns that only
from `POST /api/v1/embedding-switches/plan` (`needed: true`), or from the body
of a `409 embedding_switch_required` answer to activate.

## 4. Summary fields

### 4.1 `embedding_switch`

On `/api/v1/memory-processing` and `/api/v1/health`:
`{"job_id": "…", "kind": "switch|merge", "state": "<job state>", "percent": 50.0, "eta_s": 35}`,
or `{"kind": "merge", "state": "scanning", "percent": 0.0, "eta_s": null}` during
the startup scan, or null.

## 5. Error codes

### 5.1 Refusals

| Code | HTTP | When |
|---|---|---|
| `embedding_switch_required` | 409 (+ `plan`) | activate with another embedding on a non-empty home |
| `embedding_switch_running` | 409 | activate / start / embedding edit of the active or target profile while a job is open |
| `memory_lifecycle_busy` | 409 | start while evolve or graph backfill is running |
| `embedding_switch_plan_stale` | 409 (+ `plan`) | `plan_token` no longer matches (data or live space changed) |
| `embedding_switch_not_needed` | 409 (+ `plan`) | start with the same identity, or on an empty home (activate instead) |
| `embedding_switch_in_progress` | 409 | memory edit/delete, `POST /api/v1/graph-width` while writes are paused |
| `embedding_switch_not_cancellable` | 409 | cancel in `committing` or later |
| `embedding_switch_not_found` | 404 | unknown job, or cancel with no open job |
| `embedding_space_not_found` | 404 | unknown or already dropped space |
| `embedding_space_not_retired` | 409 | delete of a live, building or stray space |
| `embedding_space_in_use` | 409 | delete of a space an SDK client still holds open; retry later |
| `embedding_probe_required` | 409 (blocker) | target not probed at this revision (activate answers `model_profile_probe_required`) |
| `embedding_dimensions_unknown` | 409 (blocker) | target width unknown; probe it or declare `embedding_dimensions` |
| `embedding_switch_requires_migration` | 409 (blocker) | legacy `one_memory_*` collections still wait for `sdk-migrate` |
| `sdk_reembed_unavailable` | 503 (blocker) | installed SDK has no re-embed module (before hy-memory 2.0.10) |
| `insufficient_disk_space` | 507 (blocker) | free disk < 1.5 × estimated target size + 200 MB |
| `model_profile_not_found` | 404 | unknown profile |
| `model_profile_conflict` | 409 | `expected_revision` stale |
| `embedding_unavailable` | 503 | search, recall, context, library search or ask could not embed the query (remote embedding down). No lexical fallback. |
| `embedding_space_profile_missing` | 503 | the profile that fills the live space was deleted and no active profile has its model and width; activate one that does |

### 5.2 Job failure codes (`error_code` on a `rolled_back` job)

| Code | Meaning |
|---|---|
| `writes_did_not_drain` | in-flight model writes did not finish within 10 minutes |
| `embedding_service_failed` | the embedding service kept failing after retries |
| `embedding_dimensions_mismatch` | the service returned another width than probed |
| `embedding_verification_failed` | the copy did not verify (missing/changed ids, widths, per-user counts, graph) |
| `target_profile_changed` | the target profile was edited during the switch |
| `embedding_switch_cancelled` | cancelled by the user |
| `model_secret_unavailable` | the embedding API key could not be read |
| `secure_storage_unavailable` | the system keychain was unavailable |
| `embedding_switch_clients_busy` | SDK clients stayed busy; the graph could not be copied |

## 6. App guidance

### 6.1 Setup flow (`finishSetup`)

probe → `POST /embedding-switches/plan` → `needed: false`: activate as today →
`needed: true`: show the confirmation dialog; on confirm
`POST /embedding-switches` with the plan's `plan_token`. The active profile does
not change until the job is `done`. Persist `embedding_base_url` explicitly
for remote embeddings. Handle `409 embedding_switch_required` from activate
the same way (the plan is in the body).

### 6.2 Loading screen (startup merge)

Core is already ready and serving reads when the App's loading phase starts.
Poll `GET /api/v1/embedding-switches/current` (or `/api/v1/health` →
`embedding_switch`) every second. While `phase` is `scanning`, or `job.kind` is
`merge` and not terminal, show the line below with `progress.percent`. The App
may enter the home before the merge ends (reads work); then show the banner
(§6.4) instead.

| | Copy |
|---|---|
| zh | 正在检查向量数据… / 正在合并旧版本留下的向量数据 {p}%，完成前暂不整理新记忆。 |
| en | Checking vector data… / Merging vector data left by an earlier version: {p}%. New memories are processed once this finishes. |
| es | Comprobando los datos vectoriales… / Fusionando datos vectoriales de una versión anterior: {p} %. Los recuerdos nuevos se procesarán al terminar. |

### 6.3 Confirmation dialog

| | Copy |
|---|---|
| zh | **更换向量模型** 你把向量模型从 **{旧模型}（{旧维度} 维）** 换成了 **{新模型}（{新维度} 维）**。不同的向量模型无法互相检索。为了让已有记忆在新模型下仍能被找到，MemHub 会用新模型为全部记忆重新生成向量：共 **{N} 条向量**（{用户数} 个用户），预计耗时 **约 {T}**（{本地：在本机计算，不产生费用 / 远程：将调用 {endpoint}，约 {请求数} 次请求，可能产生费用}），需要额外磁盘空间约 **{X} MB**。切换期间：新的对话会照常记录，但暂不整理成记忆，切换完成后自动补上；检索和记忆库继续使用旧模型；暂时无法编辑或删除记忆。全部完成并校验通过后才会正式切换。中途失败或取消，会保持使用旧模型，不丢失任何数据。旧的向量数据会保留 14 天。[取消] [开始切换] |
| en | **Change embedding model** You changed the embedding model from **{old} ({oldDims}-d)** to **{new} ({newDims}-d)**. Vectors from different models cannot search each other, so MemHub will re-create the vectors of all memories with the new model: **{N} vectors** ({users} users), about **{T}** ({local: computed on this Mac, free / remote: calls {endpoint}, about {requests} requests, may cost money}), about **{X} MB** of extra disk. During the switch new conversations are still recorded but not turned into memories until it finishes; search and the library keep using the old model; memories cannot be edited or deleted. The switch takes effect only after everything is verified. If it fails or you cancel, the old model stays and no data is lost. Old vectors are kept for 14 days. [Cancel] [Start switch] |
| es | **Cambiar el modelo de embeddings** Cambiaste el modelo de **{old} ({oldDims} d)** a **{new} ({newDims} d)**. Los vectores de modelos distintos no se pueden buscar entre sí, así que MemHub regenerará los vectores de todos los recuerdos con el nuevo modelo: **{N} vectores** ({users} usuarios), unos **{T}** ({local: se calcula en este equipo, sin coste / remoto: llama a {endpoint}, unas {requests} solicitudes, puede tener coste}), unos **{X} MB** de disco adicional. Durante el cambio las conversaciones nuevas se registran pero no se convierten en recuerdos hasta terminar; la búsqueda y la biblioteca siguen usando el modelo anterior; no se pueden editar ni borrar recuerdos. El cambio solo se aplica tras verificarlo todo. Si falla o lo cancelas, se mantiene el modelo anterior sin perder datos. Los vectores anteriores se guardan 14 días. [Cancelar] [Iniciar cambio] |

Extra lines: same width (`source.dims == target.dims`) — zh 新旧模型维度相同，但向量不能通用，仍需重新生成。/ en Both models have the same width, but their vectors are not interchangeable, so they still have to be re-created. / es Ambos modelos tienen la misma dimensión, pero sus vectores no son intercambiables y hay que regenerarlos. Builtin target (`target_local`) — zh 将使用内置模型 bge-small-zh-v1.5，在本机计算，不产生费用。/ en Uses the built-in model bge-small-zh-v1.5, computed on this Mac at no cost. / es Usa el modelo integrado bge-small-zh-v1.5, calculado en este equipo sin coste. Strays in `sources` — zh 另外会合并旧版本留下的 {M} 条向量。/ en Also merges {M} vectors left by an earlier version. / es También fusiona {M} vectores de una versión anterior. Blockers disable [Start] and show §5.1 text.

### 6.4 Progress banner and card

| State | zh | en | es |
|---|---|---|---|
| preparing | 正在准备切换向量模型，等待正在进行的记忆整理结束… | Preparing to switch the embedding model, waiting for memory processing in progress… | Preparando el cambio de modelo, esperando a que termine el procesamiento en curso… |
| reembedding | 正在切换向量模型：重新生成向量 {done}/{total}（{p}%），预计还需 {eta}。记忆整理已暂停，检索不受影响。[取消切换] | Switching embedding model: re-creating vectors {done}/{total} ({p}%), about {eta} left. Memory processing is paused; search is unaffected. [Cancel] | Cambiando el modelo: regenerando vectores {done}/{total} ({p} %), faltan unos {eta}. El procesamiento está en pausa; la búsqueda no se ve afectada. [Cancelar] |
| verifying / committing | 正在校验新向量… | Verifying the new vectors… | Verificando los vectores nuevos… |
| done (switch) | 已切换到 {新模型}。暂停期间的对话正在整理成记忆。[清理旧向量数据] | Switched to {new}. Conversations from the pause are being processed now. [Clean up old vectors] | Cambiado a {new}. Las conversaciones de la pausa se están procesando. [Limpiar vectores antiguos] |
| done (merge) | 旧向量数据已合并。 | Old vector data merged. | Datos vectoriales antiguos fusionados. |
| failed | 切换未完成，正在恢复旧向量模型… | The switch did not finish; restoring the previous model… | El cambio no terminó; restaurando el modelo anterior… |
| rolled_back | 切换未完成，已恢复使用 {旧模型}，数据未受影响。原因：{reason} [重试] [查看日志] | The switch did not finish; {old} is in use again and no data was affected. Reason: {reason} [Retry] [View log] | El cambio no terminó; se vuelve a usar {old} sin afectar a los datos. Motivo: {reason} [Reintentar] [Ver registro] |
| rolled_back + `embedding_switch_cancelled` | 已取消切换，继续使用 {旧模型}。 | Switch cancelled; still using {old}. | Cambio cancelado; se sigue usando {old}. |

Retry = plan again and start a new job. "Clean up" calls
`DELETE /api/v1/embedding-spaces/{source.space_id}` (show `vectors`).

`{reason}` per §5.2:

| Code | zh | en | es |
|---|---|---|---|
| writes_did_not_drain | 等待正在进行的记忆整理结束超时 | Timed out waiting for memory processing to finish | Se agotó la espera del procesamiento en curso |
| embedding_service_failed | 向量服务连续请求失败 | The embedding service kept failing | El servicio de embeddings falló repetidamente |
| embedding_dimensions_mismatch | 向量服务返回的维度与探测到的不一致 | The service returned another vector width than probed | El servicio devolvió otra dimensión que la detectada |
| embedding_verification_failed | 新向量没有通过校验 | The new vectors did not verify | Los vectores nuevos no superaron la verificación |
| target_profile_changed | 切换期间目标配置被修改 | The target profile was edited during the switch | El perfil de destino se modificó durante el cambio |
| model_secret_unavailable | 读不到向量服务的 API Key | The embedding API key could not be read | No se pudo leer la clave API de embeddings |
| secure_storage_unavailable | 系统钥匙串不可用 | The system keychain is unavailable | El llavero del sistema no está disponible |
| embedding_switch_clients_busy | 记忆图谱一直被占用 | The memory graph stayed busy | El grafo de memoria siguió ocupado |

Blocked actions while writes are paused (`409 embedding_switch_in_progress`
/ `embedding_switch_running`): zh 正在切换向量模型，暂时无法修改记忆，请在切换完成后再试。/ 上一次向量模型切换还在进行，请等待完成或先取消。 en The embedding model is being switched; memories can be edited again once it finishes. / A previous switch is still running; wait for it or cancel it first. es Se está cambiando el modelo de embeddings; podrás editar recuerdos al terminar. / Aún hay un cambio en curso; espera o cancélalo primero.

### 6.5 `embedding_unavailable` toast

Any search/recall/context route answering `503 {"detail": "embedding_unavailable"}`:
show a top-right toast (do not show an empty result list).

| | Copy |
|---|---|
| zh | 向量服务暂时不可用，无法检索记忆。请检查向量模型的服务地址和 API Key。 |
| en | The embedding service is unavailable, so memories cannot be searched. Check the embedding model's endpoint and API key. |
| es | El servicio de embeddings no está disponible y no se pueden buscar recuerdos. Revisa la dirección del servicio y la clave API del modelo de embeddings. |

## 7. CLI

`memhub models activate <id> [--yes] [--wait|--no-wait] [--json]` shows the
plan; without `--yes` it prompts on a terminal and exits 2 otherwise; with
`--yes` it starts the switch and follows it (default on a terminal; `--json`
emits one JSON line per progress change, then a result line). Ctrl-C only
stops watching. `memhub models add … --activate` probes first, then the same
path. `memhub models switch status|cancel`, `memhub models spaces
[--cleanup <space-id> --yes]`, and a `向量模型：…` line in `memhub status`.
