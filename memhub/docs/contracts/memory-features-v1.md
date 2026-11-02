# SDK retrieval and extraction features v1

Status: Core + CLI implemented. Stacks on the memory-mode contract
(`memory-mode-evolve-v1.md`). The App keeps today's behaviour and has no
control for this yet; the copy in section 9 is there if it adds one.

MemHub hard-codes none of the SDK's optional features. A person can turn the
ones below on or off through the CLI or the API. Nobody has to: an unset
feature keeps the SDK default, and with nothing set the SDK config is byte for
byte what MemHub built before this setting existed.

All routes need the Runtime bearer token. Unknown query parameters and body
fields answer `422 {"detail": "request_validation_failed"}`, like every other
settings route. The setting is global, like the memory mode: it applies to
every user's SDK client and takes no `user_id`.

## 1. Inventory

These are the hy-memory flags (`hy_memory/config.py` and the modules that read
the environment directly) that change retrieval or extraction quality. Cost:
"none" means no extra call; "query" means one more local store query per
search; "LLM" means extra generation-model calls; "tokens" means a longer
prompt and no extra call.

### Exposed

| Name | SDK field (env) | Default | Mode | Cost |
|---|---|---|---|---|
| `graph_rerank` | `graph_store.graph_rerank_enabled` (`MEMORY_GRAPH_RERANK_ENABLED`) | off | ultra (graph store) | query (graph edges) |
| `same_series_timeline` | `recall.same_series_timeline_enabled` (`MEMORY_SAME_SERIES_TIMELINE_ENABLED`) | off | ultra + `graph_rerank` | none |
| `episode_recall` | `recall.episode_recall_enabled` (`MEMORY_EPISODE_RECALL_ENABLED`) | off | pro or ultra; no graph | query (episode members) |
| `session_episodes` | `recall.session_episode_enabled` (`MEMORY_SESSION_EPISODE_ENABLED`) | off | pro or ultra | none |
| `episode_summary` | `recall.episode_summary_enabled` (`MEMORY_EPISODE_SUMMARY_ENABLED`) | off | pro or ultra | LLM: one call per closed episode of 3+ memories |
| `intention` | `llm.enable_intention` (`MEMORY_ENABLE_INTENTION`) | **on** | pro or ultra | none (off shortens the prompt) |
| `intention_dedup` | `recall.intention_dedup_enabled` (`MEMORY_INTENTION_DEDUP_ENABLED`) | **on** | pro or ultra | none |
| `few_shot` | `llm.few_shot_enabled` (`MEMORY_FEW_SHOT_ENABLED`) | off | pro or ultra | tokens (extraction and reconcile) |

- **Graph rerank (usage A).** It propagates support over the graph edges
  inside the recall pool (`reader_base._apply_graph_rerank`). It needs a graph
  store, which MemHub opens only in ultra. In pro and lite it is reported
  `active: false, reason: "requires_ultra"`.
- **Episode recall (usage B).** It adds memories from the same episode as a
  hit and scores them on three channels: tag-keyword overlap, content overlap
  and temporal affinity. It needs no graph, so it works in pro (issue #121).
  It helps only memories that carry an `episode_id`.
  - Memories that System 1 adds through reconcile get an idle-gap
    `episode_id` whenever the SDK cache is available, which MemHub's SQLite
    cache is. A new episode starts after 30 minutes idle or 50 memories.
  - `session_episodes` replaces that with one stable episode per conversation.
  - Memories stored before the change keep the id they had.
  - Raw turns from lite, and notes written before episodes existed, carry none.
- **Episode write side.** `session_episodes` and `episode_summary` are the
  write half of episodes, and both are off by default. Usage B only helps once
  episodes are being written. The SDK's session narrative (L3, `finalize_session`)
  is a separate feature and is not exposed (section 2).
- **`intention`** is on by default. Turning it off stops L7 intention
  memories from being extracted.
- **`intention_dedup`** (hy-memory 2.0.12) is on by default, which is what
  every earlier version did. A search treats intentions of one user and agent
  with the same `memory_at` day, the same `valid_until` day and a cosine
  similarity at or above `intention_dedup_threshold` as copies: it returns the
  earliest and deletes the others from the store. Off, no intention dedup
  runs, at read time or within a write batch, and nothing is hidden, dropped
  or deleted.

### Parameters

Numeric settings beside the features. Each tunes one feature and acts only
while that feature acts.

| Name | SDK field (env) | Default | Range | Tunes |
|---|---|---|---|---|
| `intention_dedup_threshold` | `recall.intention_dedup_threshold` (`MEMORY_INTENTION_DEDUP_THRESHOLD`, else `MEMORY_DEDUP_THRESHOLD`) | 0.95 | 0.8–1.0 inclusive | `intention_dedup` (read time only) |

Why 0.8–1.0: with dedup on, every intention at or above the threshold is
deleted from the store at read time, and that cannot be undone. Below about
0.8 the embedding models MemHub ships or connects score different plans on
one topic as that close, so a lower value would delete intentions that are
not copies. Cosine similarity cannot exceed 1.0, and 1.0 keeps dedup for
exact copies only. Someone who wants no dedup turns `intention_dedup` off
rather than picking a threshold.

### Left out, and why

| Flag(s) | Why it is not exposed |
|---|---|
| `recall.entity_store_enabled` (`MEMORY_ENTITY_STORE_ENABLED`) | Entity extraction needs spaCy and `en_core_web_sm`, which MemHub does not ship, so it would silently do nothing. It also writes a new `{collection}_entities` collection, and MemHub's collection identity has to stay fixed. |
| `llm.enable_query_rewrite` (`MEMORY_ENABLE_QUERY_REWRITE`) | It only affects default-mode searches that pass `queries=[...]`, and MemHub never sends that. Per-request rewrite already exists as `search_mode: "agentic"` (#252). |
| `session_episode_context_enabled`, `dynamic_episode_enabled`, `series_episode_enabled`, `episode_capsule_context_enabled`, `episode_member_expansion_enabled` | They read or write the L3 session narrative that `finalize_session` builds, and MemHub never calls it. When dynamic or series finalize fails, the SDK fails the write with a non-retryable 409 partial result, which would leave MemHub jobs `unknown`. |
| `local_rerank_enabled` (+ model / device) | It downloads a cross-encoder model on first use. That is a network fetch MemHub's packaged, offline runtime and zero-collection rules do not allow. |
| `strength_enabled`, `access_tracking_enabled` | They write access counts back to memory rows on every search: a read that mutates. Internal. |
| `structure_metadata_enabled`, `retrieval_clauses_*`, `supplemental_propositions_enabled`, `hierarchy_bundle_*`, `multi_seed_bundle_*`, `tag_index_nominee_*`, `dynamic_entity_tag_embed_enabled` | Experimental retrieval lanes, each with many tuning parameters. The SDK marks them as still under gray validation, and some depend on System 2 output. Internal tuning, not a person's choice. |
| `graph_store.memory_graph_write`, `path_enabled` (default on) | Turning them off breaks graph rerank and evolve. Dangerous. |
| `hierarchy_topic_write`, `temporal_spine`, `topic_summary_enabled` | Experimental graph write lanes ("stage 5"); topic summary also costs LLM calls. Not validated for users. |
| Schema lane and catalog / supplement (`MEMORY_EVOLING_SCHEMA_DEDUP_ENABLED`, `MEMORY_EVOLING_SCHEMA_COMPACTION_ENABLED`, `MEMORY_EVOLING_BALANCED_CATALOG_ENABLED`, `MEMORY_EVOLING_SPECIFIC_SUPPLEMENT_ENABLED`, `MEMORY_USER_PROFILE_*`, `MEMORY_HYBRID_V2_*_L0_ENABLED`) | The SDK reads them from `os.environ`, not from `MemoryConfig`. Some are read once at import (`system2_tools`), others on each call for the whole process. They cannot be scoped to one client, and setting them in Core's environment would need a restart and would leak into every client. Section 3. |
| The hybrid reader's env knobs (`MEMORY_HYBRID_V2_*`, `MEMORY_TOPIC_*_ENABLED`, `MEMORY_*_BUNDLE_ENABLED`, `MEMORY_RERANK_*`) | Same reason: read from the process environment on each search. |
| `RECONCILE_ENABLED`, `SWEEPER_ENABLED`, `MEMORY_CROSS_DOMAIN_ENABLE` | Read at import time. Turning reconcile off stores every extraction without dedup. Dangerous. |
| `coding.chain_block_enabled`, `coding.verbatim_fallback_enabled` | Coding-scene options. The verbatim fallback stores assistant and tool traces as facts. |
| `MEMORY_ENABLE_GRAPH` (graph in pro) | That is the memory mode's job (`settings mode`). |
| `MEMORY_LOG_LLM_CONTENT`, pipeline log, trace, memory operations, metrics / telemetry | Diagnostics. MemHub controls them elsewhere (`diagnostic-logging.md`). |

## 2. The setting

`config.json` gains an optional `memory_features` object next to
`memory_mode`. It is written only once a person sets a feature, and then it
holds only the features they set:

```json
{"memory_mode": "pro", "memory_features": {"episode_recall": true, "intention_dedup_threshold": 0.9}}
```

Feature values are strict booleans, a parameter is a number in its range, and
unknown keys are refused. So a hand-edited file with an unknown feature or an
out-of-range threshold fails to load (`config_invalid`), like any other
malformed setting.

## 3. How a setting reaches the SDK

- Every exposed feature is a `MemoryConfig` field that the SDK reads from the
  config object when it is called. None of them is read from `os.environ` at
  call time.
- `hy_sdk_runtime.build_sdk_config(..., features=...)` sets those fields on
  each client's config after `MemoryConfig.from_dict`. The result is
  deterministic per client.
- The SDK's own env vars (the second column of the table) still decide the
  default of a feature nobody set. That is exactly what happened before the
  setting existed. A setting always wins over the env var.
- MemHub writes no env var. The flags the SDK reads straight from the
  environment are the ones left out above.
- Each client's features are fixed when it is built, like its mode. Both the
  write handles (`SdkWriteCoordinator`) and the read handle
  (`ActiveProfileReadHandle`) are keyed by (profile, revision, mode,
  features). The features part of the key is the sorted (name, value) pairs
  a person set, parameters included, so a new threshold is a new key.
- A change runs `retire_stale_handles`, the #218 use count. An idle write
  handle closes at once, and a write in flight finishes with the features it
  started with. The next read builds a client with the new key. The old read
  client closes once no request still uses it.
- Nothing stored is migrated. A feature never touches `vector_store`, the
  embedder or any store path, so the collection identity is the same with any
  features (tested).

## 4. `GET /api/v1/settings/memory-features`

```json
{
  "mode": "pro",
  "features": [
    {
      "name": "graph_rerank",
      "enabled": true,
      "configured": true,
      "default": false,
      "active": false,
      "reason": "requires_ultra",
      "side": "read",
      "requires_mode": "ultra",
      "requires_feature": null,
      "cost": "extra_query",
      "sdk_field": "graph_store.graph_rerank_enabled",
      "env": "MEMORY_GRAPH_RERANK_ENABLED",
      "description": "Usage A: propagate support over knowledge-graph edges ..."
    }
  ],
  "parameters": [
    {
      "name": "intention_dedup_threshold",
      "value": 0.95,
      "configured": null,
      "default": 0.95,
      "minimum": 0.8,
      "maximum": 1.0,
      "active": true,
      "reason": null,
      "requires_feature": "intention_dedup",
      "sdk_field": "recall.intention_dedup_threshold",
      "env": "MEMORY_INTENTION_DEDUP_THRESHOLD",
      "description": "Cosine similarity at or above which ..."
    }
  ],
  "non_default": {"graph_rerank": true}
}
```

- `enabled` is what clients are built with: `configured` if set, else `default`.
- `configured` is null when unset.
- `default` is the SDK default: its code default, or its own env var when set.
- `active` is false with a `reason` when the feature does nothing right now.
  The reason is one of `disabled`, `requires_pro` (lite), `requires_ultra`,
  `requires_graph_rerank` (for `same_series_timeline`), or
  `requires_intention_dedup` (for the threshold while `intention_dedup` is off).
- `cost` is one of `none`, `extra_query`, `llm_call`, `prompt_tokens`.
- `features` always lists every feature, in the table's order.
- `parameters` lists every parameter. `value` is what clients are built with;
  `minimum` and `maximum` are the range a `PUT` accepts. A parameter is
  active exactly when the feature it tunes is.

## 5. `PUT /api/v1/settings/memory-features`

The body has any of the feature names as keys:

- `true` or `false` sets that feature.
- A JSON number sets a parameter, if it is in range (inclusive).
- `null` resets it to the default.
- An absent key is left as it is.
- Anything else answers `422`, including a non-boolean feature, `"true"`, `1`,
  an unknown name, or a threshold outside 0.8–1.0, a string, or a boolean.

```json
{"graph_rerank": true, "intention_dedup_threshold": 0.9, "few_shot": null}
```

The answer is the state of section 4, plus `changed`: the features and then
the parameters whose effective value changed, in the tables' order. When the set of configured
features changes, the SDK clients are rebuilt (section 3). That includes a
reset to a value equal to the default, after which the client carries no
override at all.

## 6. Status

`pipeline` in `GET /api/v1/memory-processing` (memory-mode contract §7) gains
`features`, which lists the features whose effective value differs from the
default, and the parameters (as numbers) likewise. It is `{}` while every
setting is at its default:

```json
{"pipeline": {"mode": "pro", "...": "...", "features": {"episode_recall": true, "intention_dedup_threshold": 0.9}}}
```

`memhub status --json` and `memhub progress --json` carry it as
`pipeline.features`. The text status appends `；SDK 功能：episode_recall=开、…`
to the memory-mode line.

## 7. CLI

```bash
memhub settings features                                       # list them
memhub settings features set episode_recall=true session_episodes=true   # plan only
memhub settings features set episode_recall=true session_episodes=true --yes
memhub settings features set intention_dedup=false --yes        # no intention dedup
memhub settings features set intention_dedup_threshold=0.9 --yes # a looser dedup
memhub settings features reset few_shot                         # back to the default
memhub settings features reset                                  # every set feature
```

`set` prints its plan and needs `--yes`, like `settings mode`, because some
features spend model calls. `reset` only returns features to their defaults,
so it applies at once. Unknown names, feature values other than `true` /
`false`, and a threshold that is not a plain decimal in 0.8–1.0 are a usage
error (exit 2) before the Runtime is asked. Against an older Runtime that does
not list the threshold, `set` answers `rejected` /
`memory_feature_unsupported` without sending it.

## 8. Rules kept

- Global management, as for the mode: no `user_id`; reads stay scoped per
  user as before.
- Strict parameters: unknown body fields and query parameters answer 422.
- The mode semantics of #253 are unchanged. A feature needing a higher mode is
  stored and reported inactive; it is not refused.

## 9. Copy the UI can reuse

| Key | zh-CN | en | es |
|---|---|---|---|
| title | 高级检索与提取 | Advanced retrieval and extraction | Recuperación y extracción avanzadas |
| graph_rerank | 图谱重排：沿知识图谱的关联提升相关记忆的排名（仅 ultra）。 | Graph rerank: ranks memories higher when the knowledge graph links them to strong matches (ultra only). | Reordenación por grafo: sube en la lista las memorias que el grafo de conocimiento relaciona con buenas coincidencias (solo ultra). |
| same_series_timeline | 同系列时间线：同一系列的记忆按时间放在一起（需图谱重排）。 | Same-series timeline: keeps memories of one series together in time order (needs graph rerank). | Línea temporal de la serie: mantiene juntas y en orden temporal las memorias de una misma serie (requiere la reordenación por grafo). |
| episode_recall | 片段召回：补入与命中记忆同一片段的记忆，并按标签、内容和时间打分。 | Episode recall: adds memories from the same episode as a match and scores them by tags, content and time. | Recuperación por episodio: añade memorias del mismo episodio que una coincidencia y las puntúa por etiquetas, contenido y tiempo. |
| session_episodes | 片段写入：同一会话中提取的记忆归入同一片段。 | Episodes per conversation: memories extracted from one conversation share one episode. | Episodios por conversación: las memorias extraídas de una conversación comparten un episodio. |
| episode_summary | 片段摘要：片段结束时总结一次（每个片段一次模型调用）。 | Episode summaries: each finished episode is summarized once (one model call per episode). | Resúmenes de episodio: cada episodio terminado se resume una vez (una llamada al modelo por episodio). |
| intention | 意图提取：随事实一起记住你提到的计划和意图。 | Intentions: remembers the plans and intentions you mention, along with facts. | Intenciones: recuerda los planes e intenciones que mencionas, junto con los hechos. |
| intention_dedup | 意图去重：同一天里几乎相同的意图只保留最早一条，其余删除。 | Intention dedup: of near-identical intentions from one day, keeps the earliest and deletes the rest. | Deduplicación de intenciones: de las intenciones casi idénticas de un mismo día, conserva la más antigua y borra las demás. |
| intention_dedup_threshold | 去重阈值：越高，越少的意图被视为重复。 | Dedup threshold: the higher it is, the fewer intentions count as copies. | Umbral de deduplicación: cuanto más alto, menos intenciones cuentan como copias. |
| few_shot | 提取示例：在提取提示中加入示例，可帮助较弱的模型（输入更长）。 | Extraction examples: adds examples to the extraction prompt, which can help a weaker model (longer input). | Ejemplos de extracción: añade ejemplos a la instrucción de extracción, lo que puede ayudar a un modelo más débil (entrada más larga). |
| default | 默认 | Default | Predeterminado |
| requires_ultra | 当前模式下不生效，需要 ultra。 | Has no effect in this mode; needs ultra. | No tiene efecto en este modo; requiere ultra. |
| requires_pro | lite 模式下不生效。 | Has no effect in lite. | No tiene efecto en lite. |
| requires_graph_rerank | 需要先开启图谱重排。 | Turn on graph rerank first. | Activa primero la reordenación por grafo. |
| episodes_hint | 片段召回只对带片段的记忆有用；开启片段写入后，新提取的记忆才会按会话归入片段。 | Episode recall only helps memories that belong to an episode; with episodes per conversation on, new memories are grouped by conversation. | La recuperación por episodio solo ayuda a las memorias que pertenecen a un episodio; con los episodios por conversación activados, las memorias nuevas se agrupan por conversación. |
| cost_llm | 会增加模型调用 | Uses extra model calls | Usa llamadas adicionales al modelo |
| applied | 正在进行的写入按原设置完成，之后的写入和检索使用新设置。 | A write in progress finishes with the old settings; later writes and searches use the new ones. | Una escritura en curso termina con la configuración anterior; las siguientes escrituras y búsquedas usan la nueva. |
