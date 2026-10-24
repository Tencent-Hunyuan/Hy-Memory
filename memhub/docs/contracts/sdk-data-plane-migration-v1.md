# SDK Data Plane Migration v1

Updated: 2026-10-24 (vector reattach, readback).

This contract moves released MemHub installations from the historical
Evidence/Governance authority to the Hy Memory SDK data plane defined by
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

It is an offline migration executed inside the App's visible startup loading
phase, before Core accepts product traffic. It is not a dual-read or dual-write
design and not a separate manual upgrade flow for ordinary users.

## 1. Why no memory is copied

Every released build (`v1.0.0` through build 19) wrote memory through SDK
`add()` / `add_extracted()` first. The SDK stored the raw turn (L1 raw) and the
extracted Chat / Coding Experience / Working Fact memories in its own stores.
The Governance and Evidence tables were a mirror that MemHub read back from the
SDK afterwards.

The SDK stores are therefore already complete. The migration copies no memory
and no raw turn, and does not compare the mirror with the SDK:

- the mirror's layer labels did not record which Coding store held a row, and
  the SDK physically deletes experiences its reconciler retires, so the mirror
  cannot be trusted to decide what the SDK should contain;
- the product has no user editing of memories in practice, so the mirror holds
  no user intent the SDK lacks;
- SDK L1 raw is retained for three days by the Runtime's own
  `prune_expired_raw`, not by the migration.

"Already complete" holds only if the new Runtime opens the same stores. Builds
before #145 named a profile's vector collections `one_memory_<12 hex>…`; #145
renamed them `memhub_<16 hex>…` without carrying the old ones over, so later
Runtimes read an empty collection (build 20 cut over showing 37 of 3317
memories). The migration therefore reattaches those collections (§3, step 3)
and proves the new Runtime reads the memories back before it cuts over
(step 4).

## 2. Authority before and after cutover

Before the cutover marker is committed, legacy tables remain the product read
authority; a Runtime that was never upgraded through Desktop or `memhub
install`/`upgrade` still serves them. Desktop does not start Runtime while
migration is incomplete.

After the cutover marker is committed:

- SDK stores are the only memory data authority;
- MemHub stores management state and `memory_id` / raw references only;
- legacy Evidence/Governance/retrieval/session-shadow and Matter tables are
  dropped before the normal App window is revealed;
- startup never falls back to legacy authority.

The surviving `memory_authority_state` singleton, not table existence, selects
the authority.

A fresh home bootstraps the latest management schema with SDK authority
directly and never creates legacy tables.

## 3. Who runs it, and how

One Core command does the whole migration:

```text
one-memory sdk-migration-status --json      # read-only: is there work?
one-memory sdk-migrate --runtime-build <build> --yes --json
```

`sdk-migrate` requires a stopped Runtime. It takes the host capture fence
(hooks return silently instead of writing Evidence while its PID is alive),
then:

1. **bundle**: copies `one-memory.sqlite3`, the SDK data plane and non-secret
   configuration into a versioned bundle and verifies it by an isolated
   restore; an existing verified bundle of the same run is reused;
2. **prepare**: applies the management schema migrations, discards SDK staging
   left by builds through 19 (§5) and makes legacy queue work self-contained
   (§4);
3. **vectors**: for the active embedding profile, renames every
   `one_memory_<old hash>…` collection to the current `memhub_<hash>…` name
   through Chroma's public client. A current collection that already holds
   writes is merged in first (newer record wins on a shared id); counts are
   checked after each step;
4. **verify**: opens the public SDK client exactly as the Runtime will and reads
   the legacy confirmed memory ids back (`list_memories`, then `get`). Every
   chat memory must read back. Coding/Working may be fewer, because the SDK's
   Coding reconciler deletes experiences it retires, but not zero when the
   legacy mirror has some. Otherwise the run stops with
   `sdk_migration_memory_unreadable` and the counts, before anything
   irreversible; legacy authority and tables stay intact;
5. **cutover**: re-verifies the bundle, repeats §4, commits SDK authority;
6. **cleanup**: drops the legacy tables (including the `evidence_search`
   index) and runs `VACUUM` (skipped if the disk is too full).

A home with legacy memories but no active model profile is not migrated
(`sdk_migration_model_profile_required`): its collections can be neither named
nor read back.

A home already on SDK authority still reports `required` while legacy-named
vector collections exist for its profile (build 20 homes). `sdk-migrate` then
reattaches them and reads back against the memory ids in the pre-cutover
bundle; that report is recorded but does not block, as nothing irreversible is
left. Runtime code never renames or reads Chroma directly.

With `--json` each step prints `{"state":"running","step":…}` and the last line
is the result: `cutover`, `complete` (only cleanup was left), `not_required`, or
`failed` with a stable `code`. Every step is safe to repeat; a retry resumes the
newest unfinished `sdk-authority-v2-*` run and its bundle, including a run that
build 19 left in `review_required/blocked`.

Callers only keep the Runtime stopped, and its service from restarting, around
that command (`runSdkMigration` in `packages/runtime-control`):

- **Desktop** runs it in the startup loading phase before attaching the UI; a
  failure keeps the App closed with the code's sentence.
- **CLI `memhub install` / `memhub upgrade`** (macOS and Linux) run it with the
  new Runtime's Core after switching `current` and before starting the service.
  A failure before cutover rolls back to the previous version. After cutover an
  older Runtime refuses the home, so a later readiness failure keeps the new
  version and reports `runtime_not_ready_after_sdk_migration`.

After cutover, History catch-up scans forward from each source checkpoint and
recovers turns skipped while capture was fenced.

## 4. Queue work

Queue rows remain MemHub management state. Before the legacy tables disappear:

- each `memory_write_jobs` row that only names Evidence IDs gets those records
  frozen into `payload_json`;
- unstarted legacy `memory_tasks` become
  `memory_write_jobs(idempotency_key="legacy-task:<task_id>")`;
- Evidence waiting in `evidence_pending_memory_dispatch` (its source cursor has
  already passed it) becomes one write job per conversation.

Legacy work that cannot continue does not block cutover; its counts are
reported in the `sdk-migrate` / cutover receipt as `abandoned`:

- a legacy task that may already have reached the SDK (`unknown`, or `running`
  with an SDK invocation) is not replayed: the SDK already holds whatever it
  committed;
- pending legacy user deletions are not carried over.

Surviving `memory_write_jobs` with `hy_invoked=1` keep their normal
post-cutover handling through SDK `get_write_outcome(request_id)`.

## 5. Staging left by earlier builds

Builds through 19 copied the SDK root to `<home>/.sdk-migration/` and swapped
directories at cutover. Before SDK authority, a leftover
`hy-memory/.<namespace>.pre-migration-*` directory is the original store and is
restored; after SDK authority it is stale and removed. More than one such
directory before authority is ambiguous and fails closed
(`sdk_migration_swap_state_ambiguous`).

## 6. Failure and rollback

Any failure leaves the marker absent and reports a stable code to the startup
dialog (for example `sdk_migration_disk_space_insufficient`,
`sdk_migration_bundle_copy_failed`, `sdk_migration_preflight_failed`,
`sdk_migration_cutover_failed`). The next startup retries.

After cutover but before any SDK-only write, rollback restores the bundle
together with the matching old Runtime. After an SDK-only write, automatic
binary-only rollback is forbidden; restoring the pre-cutover bundle loses the
post-cutover writes and must be explicit. An old Runtime must fail closed on a
post-cutover home. Changing only the Runtime `current` symlink is not data
rollback.

## 7. Acceptance

The release gate covers:

- a fresh install with no legacy table names;
- a legacy home reaching cutover with its SDK stores byte-for-byte unchanged;
- a home left blocked by build 19 (quarantined ledger and staging copy)
  completing on the next startup;
- CLI upgrade migrating before restart, rolling back on a failed migration,
  and keeping the new version when a migrated home is slow to come up;
- host capture fenced for the whole command;
- legacy-named vector collections reattached (with merge) before verifying,
  and on a home that already cut over;
- readback refusing a cutover the new Runtime cannot read, and tolerating
  retired Coding experiences;
- `/api/v2/memory-authority` agreeing with the API gates on fresh, migrated
  and legacy homes;
- pending job freezing, legacy task and pending dispatch conversion;
- invoked/unknown legacy tasks and pending deletions not blocking cutover;
- interrupted directory swap recovery before and after authority;
- bundle creation, reuse and restore;
- physical absence of every legacy table after cutover.
