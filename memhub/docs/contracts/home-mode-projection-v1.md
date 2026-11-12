# Home mode projection v1

Status: Core, contracts, and Web adoption implemented. The Web home consumes
this projection as its authority and uses the legacy summary only for older
Core builds; the CLI has no home surface and does not consume this endpoint.

## `GET /api/v2/home-projection`

Bearer token required. Optional `user_id` defaults to `local`.

Core owns this projection. Consumers do not inspect SDK raw or memory records
and do not branch on storage details.

```json
{
  "projection_version": 1,
  "user_id": "local",
  "effective_mode": "lite",
  "capabilities": {
    "content_plane": "raw",
    "generation_required": false,
    "graph_enabled": false,
    "evolve_available": false
  },
  "visible_count": 42,
  "display_state": "excerpt",
  "latest": {
    "type": "raw",
    "raw_id": "raw-1",
    "session_id": "session-1",
    "display_state": "excerpt",
    "time": "2026-11-03T08:00:00Z",
    "time_basis": "conversation",
    "excerpt": "ship the release on Friday",
    "truncated": false,
    "source_id": "cursor"
  },
  "observed_at": "2026-11-03T08:01:00Z"
}
```

`display_state` is `empty | excerpt | no_user_excerpt |
no_displayable_memory`. `latest` is null for `empty`, and also for
`no_displayable_memory` when the SDK reports rows but none of the ten returned
candidates satisfies this projection's public shape. Its discriminant is
`type: raw | memory`.

- Both arms have `time`, `time_basis`, `excerpt`, `truncated`, and `source_id`.
- `time_basis` is `conversation | import | write | update | null`.
- Raw adds `raw_id`, `session_id`, and
  `display_state: excerpt | no_user_excerpt`.
- Memory adds `memory_id`, `scene: chat | working | coding`,
  `kind: chat | experience`, and `variant: working | coding | null`. Facts are
  not in the SDK's `scene=all` list or its total.

## Read semantics

Lite performs one SDK list read whose projection payload is capped at ten:
`layers=["l1_raw"]`,
`statuses=["active","shadow"]`, newest first, K=10. The SDK page total is the
real `visible_count`. Core checks those ten rows for the newest displayable
user excerpt. If none has one, `latest` is the newest row with
`display_state=no_user_excerpt` and an empty excerpt.

Pro and Ultra perform one
`list_memories(scene="all", statuses=["active"], order="desc", limit=10)`
call. Count and latest candidates come from that same response, so they cannot
belong to different snapshots. Invalid rows are skipped within those ten;
`no_displayable_memory` is explicit if no candidate remains.

K=10 bounds only the response payload and Core projection work. In the current
SDK the chat lane still scans its backing vector store before sorting; this
contract does not claim storage-layer boundedness. Working/coding use the
SDK's indexed prefix paging.

Memory `time` is the same key `scene=all` used to rank the returned item:
chat uses `gmt_modified`/`updated_at`, falling back to `gmt_created`;
working/coding uses `updated_at`, falling back to `created_at`. `memory_at`
does not order this projection.

An SDK acquisition, read, or response-shape failure answers
`503 {"detail":"home_projection_read_failed"}`. Core never turns a failed read
into an empty projection.

## Excerpt privacy

Raw excerpts are a pure projection using the same balanced, position-aware
source-envelope parser as import:

1. Parse the SDK turn and retain only `role=user`.
2. Unwrap validated host sections, including `system_reminder`,
   `system-reminder`, `user_info`, `rules`, `agent_transcripts`,
   `additional_data`, `working_memory_reminder`, `attached_files`, and
   `image_local_path`. The contents of an unambiguous `user_query` remain.
   Unclosed, nested or otherwise ambiguous envelopes fail closed.
3. Flatten whitespace and skip empty or known control-only placeholders.
4. Keep short sentences, URLs, and paths.
5. Truncate to 200 Unicode code points.

The response never includes raw `content`, parsed messages, or custom metadata.
The old `GET /api/v2/home-summary` remains available unchanged.
