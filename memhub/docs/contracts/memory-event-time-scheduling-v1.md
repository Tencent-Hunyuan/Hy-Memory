# Memory Event-Time Scheduling v1

## Purpose

MemHub imports several Harnesses concurrently, but the resulting memories
belong to one user timeline. Queue insertion order is not sufficient: a newer
conversation can be discovered before an older file from another Harness.

This contract orders durable memory work by the time of the complete
conversation turn while retaining bounded cross-Harness concurrency.

## Write-job identity and time

One `memory_write_jobs` row represents one final `_conversation_chunks` chunk.
The row stores:

- `event_at`: UTC event time used for scheduling;
- `time_quality`: `exact`, `anchored`, `session`, or `approximate`;
- `queue_seq`: a deterministic tie-breaker only when event times are equal;
- `session_key` and `source_key`: the existing session/Harness identities.

Time selection is:

1. the protocol `user` turn timestamp;
2. the nearest session timestamp anchor;
3. the session/file timestamp;
4. `imported_at` or write creation time.

All chunks produced from one oversized turn share its opener time. Their
existing `queue_seq` order is preserved. Timestamps are scheduling metadata,
not part of the raw record or write idempotency identity.

### Cursor

Cursor transcript rows often have no JSON timestamp and one session may span
days. MemHub parses the leading host envelope:

```text
<timestamp>Friday, Oct 16, 2026, 12:24 AM (UTC+8)</timestamp>
```

before removing it from visible text. Rows without an envelope retain the
stable file-mtime/ordinal fallback and are marked as session/approximate time.

## Fixed ten-minute windows

The durable drain persists one active scheduling window:

```text
[oldest eligible event_at, oldest eligible event_at + 10 minutes)
```

The anchor is fixed until every eligible pending/running job in that range is
settled or parked. It never slides from `T` to `T+9` to admit `T+18`.

Within one window:

- different Harnesses may run concurrently, up to the existing concurrency
  limit;
- one Harness still has at most one running job;
- each Session is ordered by `(event_at, queue_seq)`;
- different Sessions from one Harness never block each other's event order;
- `source_key` limits one running job per Harness but does not define FIFO;
- jobs are selected globally by `(event_at, queue_seq, job_id)`;
- `live` affects service admission only and does not jump the time window.

When the active window is empty, the next oldest eligible pending job becomes
the new anchor. The active anchor is stored in the business database so a
restart cannot silently widen or slide the window.

## First-import barrier

During onboarding, all accepted Harness history jobs first:

1. scan source records inside the shared history cutoff;
2. form complete-turn chunks;
3. submit self-contained durable write jobs to the SDK write path.

The drain does not claim model work until every accepted history job is
terminal. A failed Harness is recorded and excluded; it does not block all
other sources forever.

The barrier is persisted through `onboarding_state` and `import_jobs`, so
restart recovery cannot bypass it. Existing users with no active onboarding
batch are not held.

## Pauses and failures

- Global sedimentation off: imports and enqueue continue; no job is claimed.
- Harness disabled/revoked: its jobs are parked/excluded from the active
  timeline and do not block other Harnesses.
- Re-enable: old jobs re-enter by their original `event_at`.
- Claimed but not invoked: return to pending without consuming an attempt.
- Already invoked: the current provider call may finish; it is never blindly
  cancelled or retried.
- Unknown outcomes retain their complete frozen input, never block another
  Session or Harness, and follow the bounded reconcile-before-retry contract.

## Immutable job input and branches

Every new `memory_write_jobs.payload_json` stores the complete immutable
messages and source metadata used by the write, not references to another
table. Execution and unknown reconciliation use that snapshot. Successful
terminal jobs retain IDs and outcome while their input body is reaped.

When a Harness rewinds and continues from an earlier point, already stored
SDK raw records and jobs are not deleted. Shared unchanged messages deduplicate;
changed or new branch records append new jobs. A later revision of a reused
host message ID receives a new write ID while the earlier terminal receipt
remains unchanged.

## Upgrade compatibility

Legacy jobs are assigned:

1. the latest `role=user` legacy source-record time linked to the job;
2. otherwise the earliest linked source-record time;
3. otherwise the original job creation time.

They are marked `approximate` and retain `queue_seq` as a deterministic
tie-breaker. Old id-only payloads remain readable; all newly submitted jobs
are self-contained.

