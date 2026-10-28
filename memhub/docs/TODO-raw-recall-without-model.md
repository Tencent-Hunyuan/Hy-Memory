# TODO: recall imported conversations before a model is configured

Status: open. Found 2026-10-28 by the Linux lifecycle run on build 35.

## Problem

A new user who has imported conversations but not configured a generation
model gets nothing from `MemHub_recall` or `memhub recall`.

`recall_with_fallback` (`apps/core/src/one_memory/recall_fallback.py`) is
meant to cover this case: without a model it searches the SDK raw layer
(`l1_raw`), the imported conversations themselves. But under SDK authority
nothing writes that layer until a model is active:

- File import and live capture only stage conversations into
  `memory_write_jobs` (assembling, then pending).
- The durable write drain claims a job only when
  `_sedimentation_claim_enabled` holds, which requires an active model
  profile (`api.py`).
- The SDK writes `l1_raw` only inside `client.add`, which the drain calls.

So until a model is configured, the raw layer is empty and the fallback has
nothing to search. Before the SDK cutover, imported Evidence lived in
MemHub's own database and was searchable without a model.

The Linux lifecycle verification used to assert "import, then recall finds
it" on a machine with no model. It now asserts that the import reached the
write queue instead (`scripts/platform/linux/verify-linux-lifecycle.mjs`,
`waitForQueued`). Restore the recall assertion once this is fixed.

## Direction

Write imported conversations to the SDK raw layer without a generation
model, and leave extraction for when a model is configured.

- The raw layer needs embeddings only. The bundled local ONNX model
  (`builtin_retrieval_snapshot`) can produce them, so no network model is
  needed.
- SDK: a public raw-only write, for example `add(..., infer=False)` or
  `add_raw(messages, ...)`, that stores `l1_raw` with source, session,
  workspace and `memory_at`, and returns its raw ids. It must be idempotent
  by `request_id`, like `add`.
- MemHub: split the claim gate. Without a model, the drain may claim a
  pending job for a raw-only write. Once a model is active, the same job
  still needs extraction, and that write must not store `l1_raw` again.
  Either the job records that its raw part is done, or extraction runs
  through `add_extracted` / a mode that skips the raw store.
- The source sync switch and grant still apply, exactly as for extraction.

## Open questions

- Should raw-only ingestion run at all when the user has turned
  sedimentation off, or does that switch mean "store nothing"?
- Storage cost: raw ingestion of a full history import without a model
  embeds every turn up front. Check the time and disk cost on a large
  history.
- When the user configures a model later, extract from the stored raw rows,
  or re-read the jobs' frozen inputs? The second keeps one write path.

## Done when

- On a fresh install with no model, `memhub recall "<phrase from an
  imported conversation>"` returns that conversation with `mode: "raw"`.
- Configuring a model afterwards extracts memories from the same
  conversations without duplicating their raw rows.
- `verify-linux-lifecycle.mjs` asserts recall again.
