# Native Hermes memory provider

`hy_memory` implements the Hermes MemoryProvider lifecycle (contract v0.21.6).
It uses Python's standard-library HTTP client; Hermes needs no Hy Memory SDK,
embedded database, model installation, or server autostart. Run Hy Memory as a
separate, operator-managed service, listening on `127.0.0.1:19527` by default.

## Install

Copy the **inner directory**, including its manifest, into the active profile:

```sh
mkdir -p "$HERMES_HOME/plugins"
cp -R plugins/hermes/hy_memory "$HERMES_HOME/plugins/hy_memory"
```

Set these variables in the environment used to start Hermes:

```sh
export HY_MEMORY_URL=http://127.0.0.1:19527
export HY_MEMORY_USER_ID=your-explicit-owner-id
export HY_MEMORY_AGENT_ID=your-explicit-agent-id
```

Set `memory.provider: hy_memory` in the active Hermes configuration and restart
Hermes. Each Hermes profile must explicitly configure its intended owner and
agent. There is no default identity or OS username fallback. IDs are fixed for
an instance; changing them requires a fresh instance/restart. `hermes_home` and
`platform` are accepted from Hermes; no local persistent state is created.

The plugin registers JSON-returning `hy_memory_search(query, limit?)` and
`hy_memory_add(text)`. Tools cannot override identities or session IDs. Add sends
all three IDs; search always filters both owner and agent and caps the result
count. Recall intentionally spans sessions within that owner/agent pair. No
unscoped get, list, or delete tools are exposed.

## Configuration and lifecycle

| Environment variable | Default | Accepted values |
| --- | --- | --- |
| `HY_MEMORY_USER_ID` | required | nonblank string, at most 256 characters |
| `HY_MEMORY_AGENT_ID` | required | nonblank string, at most 256 characters |
| `HY_MEMORY_URL` | `http://127.0.0.1:19527` | origin only, no credentials/path/query/fragment |
| `HY_MEMORY_CA_FILE` | unset (OS trust) | explicit PEM CA file, e.g. `/etc/hy-memory/ca.crt` for a private cluster CA |
| `HY_MEMORY_API_KEY` | unset | optional Bearer key for an authenticated reverse proxy |
| `HY_MEMORY_TIMEOUT` | `5` | finite seconds, 0.1–30 |
| `HY_MEMORY_WRITE_TIMEOUT` | inherits `HY_MEMORY_TIMEOUT` | finite seconds, 0.1–120; applies only to add writes |
| `HY_MEMORY_LIMIT` | `5` | integer, 1–20 |
| `HY_MEMORY_MAX_CHARS` | `4000` | memory content character budget, integer 256–16000 |
| `HY_MEMORY_MAX_BODY_BYTES` | `262144` | response byte cap, integer 1024–1048576 |
| `HY_MEMORY_AUTO_RECALL` | `false` | `true` or `false` |
| `HY_MEMORY_AUTO_CAPTURE` | `false` | `true` or `false` |

Automatic recall and capture are **opt-in**. Capture sends completed user and
assistant text to the service, where the SDK performs extraction. Explicit add
and automatic capture are suppressed in cron, subagent, and flush contexts.
Other lifecycle hooks do not write. Consider consent and retention before enabling
capture; shared-chat participant identity is not automatically mapped to owners.

`is_available` checks configuration only, without network access. The system
prompt is static. Opt-in `prefetch` returns only a matching cached query/session
or queues work and returns immediately; `queue_prefetch` warms the same cache.
A miss can therefore provide no recall this turn. The next matching call consumes
the result. Session switches invalidate recalled content, including in-flight
results. Queued writes retain their original session ID. One context-bound worker
and an eight-job queue bound concurrency; each job binds the caller's Hermes
contextvars. Shutdown discards pending jobs, invalidates recall, and waits up to
the HTTP timeout plus one second for the worker. In-flight HTTP cannot be undone.

Background failures and queue overflow appear as sanitized codes in `last_error`;
conversation hooks fail safely. `recall_status()` describes only the most recent
prefetch injection. Explicit tools return `{"success": false, "error": "..."}`
on failure; valid empty search returns `{"success": true, "memories": []}`.
Writes are never retried automatically: a timeout may mean an ambiguous write.
Successful adds include `memory_id` only when the backend returns a canonical
UUID; other IDs and arbitrary backend metadata are omitted. Search results
retain only bounded content. Recall is marked as **untrusted reference data**, never system instructions.

## Network security

**The bare Hy Memory HTTP server does not authenticate or authorize clients.**
Owner, agent, and opaque memory IDs are filtering identifiers, not authorization.
Anyone reaching the bare server can invoke its other endpoints. A Bearer header
sent to that server is ignored and provides no protection. Do not expose it to
untrusted clients, including on shared hosts without suitable isolation.

Remote plain HTTP fails closed. Remote deployment requires HTTPS to a TLS/auth
reverse proxy, gateway authorization appropriate to the owner/agent boundary,
and network policy preventing direct server access. Configure
`HY_MEMORY_API_KEY` only for a gateway that actually verifies it. HTTPS alone does
not establish authorization. Optional `HY_MEMORY_CA_FILE` loads and validates a
provider-scoped CA context once at construction. Missing, unreadable, or malformed
files make the provider unavailable with `invalid_configuration`. Certificate and
hostname verification remain required; no global TLS settings are changed. When
unset, HTTPS retains its existing OS trust behavior. Trust rotation requires a new
provider instance; its identity includes a SHA-256 fingerprint of loaded CA
certificates, without paths or PEM contents. The read timeout is a total response-read deadline; `HY_MEMORY_WRITE_TIMEOUT` provides a separate bounded deadline for add writes,
reapplied before every socket read (including headers and chunk framing). DNS resolution and connection/TLS
setup still follow platform/per-operation timeouts; this is not a hard deadline
for those operating-system operations. No timeout threads are created.
The client disables ambient proxies, refuses all
redirects to avoid forwarding credentials, and never includes backend response
text, request text, credentials, or URLs in failure messages.

The current SDK can itself turn some internal reader failures into a normal
empty search response. This plugin cannot distinguish those from legitimate
empty results; service-side observability is needed. HTTP failures, malformed
responses (including truncated declared-length or chunked bodies), explicit
`success:false`, and failed adds are reported as failures.

## Offline tests

```sh
PYTHONPATH="$PWD" python -m pytest plugins/hermes/tests -o addopts='' -q
python -m ruff check plugins/hermes
```

When `HERMES_MEMORY_PROVIDER_SNAPSHOT` is set, tests load that explicit snapshot
path with importlib. Otherwise, tests use the installed `agent.memory_provider`,
falling back to a minimal fixture when Hermes is absent. Snapshots are neither
bundled nor automatically downloaded. Registration requires
one provider instance. Local socket tests also exercise drip-fed HTTP bodies.
They exercise protocol and isolation, not live service behavior or actual recall quality. Production imports the Hermes base directly.
