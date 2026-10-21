# Memory Event-Time Scheduling Implementation Plan


> **架构变更（2026-10-21）：** 现行权威见 [`ARCHITECTURE.md`](ARCHITECTURE.md) **Top Rules**。~~MemHub Evidence / `governed_*` 作原文或 memory 权威~~ 已废弃为目标；memory 读写与 backend 只经 SDK；MemHub 可存 `memory_id` 引用。下文未逐段删线的历史表述，凡与 Top Rules 冲突处以架构文档为准。

## Goal

Preserve one user memory timeline across all imported Harnesses without
serializing unrelated work that happened at nearly the same time.

The target scheduler uses final conversation chunks as durable jobs, orders
them by turn event time, and permits cross-Harness concurrency only inside one
fixed ten-minute event-time window.

## Delivery sequence

1. **Turn time**
   - Parse native Harness timestamps.
   - Parse Cursor `<timestamp>` envelopes before stripping them.
   - Mark fallback quality explicitly.
   - Derive each chunk's time from its protocol `user` opener.

2. **Durable schema**
   - Add `event_at` and `time_quality` to `memory_write_jobs`.
   - Backfill existing jobs without changing idempotency identity.
   - Persist the active window anchor/end across restarts.

3. **Windowed Drain**
   - Anchor at the oldest eligible pending event.
   - Admit only `[anchor, anchor + 10 minutes)`.
   - Keep one running job per Harness; order globally by `event_at`.
   - Use `queue_seq` only to break equal-time ties within one Session.
   - Advance only after the current window has no eligible pending/running
     work.

4. **First-import gate**
   - Import all accepted Harness history and enqueue chunks first.
   - Release model processing after all accepted history jobs are terminal.
   - Exclude failed and user-disabled sources rather than blocking forever.

5. **Pause/restart**
   - Sedimentation off leaves jobs pending and returns unspent attempts.
   - Source pause parks that source without blocking others.
   - Restart resumes the persisted window, not a newly computed sliding one.

6. **Verification**
   - Cross-day Cursor session.
   - Exact ten-minute boundary and non-sliding windows.
   - Harness/session FIFO under concurrency.
   - Manual Memory behind historical backlog.
   - First-import zero model calls before gate release.
   - Legacy database backfill and restart recovery.

## Non-goals

- No HY Memory SDK change.
- No change to Evidence or Write Job idempotency.
- No live-priority jump across event-time windows.
- No destructive migration of old timestamps; uncertain legacy values remain
  explicitly approximate.

The normative behavior is documented in
[`contracts/memory-event-time-scheduling-v1.md`](contracts/memory-event-time-scheduling-v1.md).

